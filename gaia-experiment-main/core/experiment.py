from .parameter import Parameter

import datetime
import json
import logging
import os
import time

PCAP_WARMUP_SEC = 0.5


class ExperimentParameter(Parameter):
    """
    Handler for experiment parameters stored in configuration files.
    The following parameters are common (and thus usable) by all experiments.
    If you want to add experiment-specific parameters, you should extend this
    class.

    Attribute:
        default_parameters  Default values for the parameters
    """
    RMEM = "rmem"
    WMEM = "wmem"
    MPTCP_ENABLED = "mptcpEnabled"
    SCHED = "sched"
    CC = "congctrl"
    AUTOCORK = "autocork"
    EARLY_RETRANS = "earlyRetrans"
    KERNELPM = "kpm"
    KERNELPMC = "kpmc"  # kernel path manager client / server
    KERNELPMS = "kpms"
    USERPMC = "upmc"
    USERPMS = "upms"  # userspace path manager client / server
    USERPMC_ARGS = "upmc_args"
    USERPMS_ARGS = "upms_args"
    PCAP = "pcap"
    CLIENT_PCAP = "clientPcap"
    SERVER_PCAP = "serverPcap"
    PCAP_NODES = "pcap_nodes"
    CLIENTS = "clients"
    SERVERS = "servers"
    SNAPLEN_PCAP = "snaplen_pcap"
    XP_TYPE = "xpType"
    PING_COUNT = "pingCount"
    PRIO_PATH_0 = "priority_path_0"
    PRIO_PATH_1 = "priority_path_1"
    BACKUP_PATH_0 = "backup_path_0"
    BACKUP_PATH_1 = "backup_path_1"
    BUFFER_AUTOTUNING = "bufferAutotuning"

    # Global sysctl keys
    SYSCTL_KEY = {
        RMEM: "net.ipv4.tcp_rmem",
        WMEM: "net.ipv4.tcp_wmem",
    }

    # sysctl keys specific to client and server, independently (Legacy v0.9x)
    SYSCTL_KEY_CLIENT = {
        KERNELPMC: "net.mptcp.mptcp_path_manager",
    }
    SYSCTL_KEY_SERVER = {
        KERNELPMS: "net.mptcp.mptcp_path_manager",
    }

    # Default values for unspecified experiment parameters
    DEFAULT_PARAMETERS = {
        RMEM: "10240 87380 16777216",
        WMEM: "4096 16384 4194304",
        MPTCP_ENABLED: "1",
        KERNELPM: "fullmesh",
        KERNELPMC: "fullmesh",
        KERNELPMS: "fullmesh",
        USERPMC: "fullmesh",
        USERPMS: "fullmesh",
        USERPMC_ARGS: "",
        USERPMS_ARGS: "",
        CC: "cubic",
        SCHED: "default",
        AUTOCORK: "1",
        EARLY_RETRANS: "3",
        BUFFER_AUTOTUNING: "1",
        PCAP: "no",
        CLIENT_PCAP: "no",
        SERVER_PCAP: "no",
        PCAP_NODES: "",
        SNAPLEN_PCAP: "65535",  # Default snapping value of tcpdump
        XP_TYPE: "none",
        PING_COUNT: "50",
        PRIO_PATH_0: "0",
        PRIO_PATH_1: "0",
        BACKUP_PATH_0: "0",
        BACKUP_PATH_1: "0",
    }

    def __init__(self, parameter_filename):
        super(ExperimentParameter, self).__init__(parameter_filename)
        self.default_parameters.update(ExperimentParameter.DEFAULT_PARAMETERS)


class MptcpGeneration:
    UPSTREAM = "UPSTREAM"
    LEGACY_V09X = "LEGACY_V09X"
    NO_MPTCP = "NO_MPTCP"


class Experiment(object):
    """
    Base class to instantiate an experiment to perform.

    This class is not instantiable as it. You must define a child class with the
    `NAME` attribute.
    """
    PARAMETER_CLASS = ExperimentParameter
    IP_BIN = "ip"
    mptcp_generation = None
    output_dir = "output"
    sysctl_to_restore = {}
    node_sysctl_to_restore = {}
    unapplied_parameters = []

    def __init__(self, experiment_parameter_filename, topo, topo_config, output_dir=None, **kwargs):
        """
        Instantiation of this base class only load the experiment parameter
        """
        self.experiment_parameter = self.__class__.PARAMETER_CLASS(
            experiment_parameter_filename)
        self.topo = topo
        self.topo_config = topo_config
        if output_dir is None:
            timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            output_dir = os.path.join("output", timestamp)
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        if hasattr(self.topo, 'set_output_dir'):
            self.topo.set_output_dir(self.output_dir)
        logging.info("Saving experiment outputs to {}".format(self.output_dir))

        self.sysctl_to_restore = {}
        self.node_sysctl_to_restore = {}
        self.unapplied_parameters = []
        self.selective_pcap_procs = []
        self.mptcp_generation = kwargs.get("mptcp_generation", None)

    @property
    def ping_output(self):
        return os.path.join(getattr(self, 'output_dir', 'output'), "ping.log")

    def load_parameters(self):
        pass

    def detect_mptcp_generation(self):
        """
        Detect whether host kernel runs upstream MPTCP (Linux 5.6+),
        legacy out-of-tree MPTCP v0.9x, or lacks MPTCP support.
        """
        curr_gen = getattr(self, 'mptcp_generation', None)
        if curr_gen is not None:
            return curr_gen

        if not hasattr(self, 'topo') or not hasattr(self.topo, 'command_global'):
            return MptcpGeneration.NO_MPTCP

        legacy_val = self.topo.command_global("sysctl net.mptcp.mptcp_path_manager")
        if legacy_val and "net.mptcp.mptcp_path_manager" in str(legacy_val):
            return MptcpGeneration.LEGACY_V09X

        upstream_val = self.topo.command_global("sysctl net.mptcp.enabled")
        if upstream_val and "net.mptcp.enabled" in str(upstream_val):
            return MptcpGeneration.UPSTREAM

        return MptcpGeneration.NO_MPTCP

    def classic_run(self):
        """
        Exception-safe experiment lifecycle: prepare -> run -> finally clean (BE-1).
        """
        try:
            self.prepare()
            self.run()
        finally:
            self.clean()

    def prepare(self):
        """
        Prepare environment for the experiment.
        """
        self.setup_sysctl()
        self.setup_mptcp()
        self.run_userspace_path_manager()
        self.put_priority_on_paths()

        if self.experiment_parameter.get(self.experiment_parameter.PCAP) == 'yes':
            self.run_tcpdump()
        self.run_selective_pcap()
        self.run_netem_at()

    def put_priority_on_paths(self):
        """
        Configure path priority and backup status for MPTCP paths in a topology-neutral manner (BE-16).
        """
        prio_0 = self.experiment_parameter.get(ExperimentParameter.PRIO_PATH_0)
        prio_1 = self.experiment_parameter.get(ExperimentParameter.PRIO_PATH_1)
        backup_0 = int(self.experiment_parameter.get(ExperimentParameter.BACKUP_PATH_0))
        backup_1 = int(self.experiment_parameter.get(ExperimentParameter.BACKUP_PATH_1))

        # Check if legacy switch accessor is supported by topo_config
        has_switch_accessor = hasattr(self.topo_config, 'get_router_interface_to_client_switch')
        client_node = getattr(self.topo_config, 'client', None)
        router_node = getattr(self.topo_config, 'router', None)

        if not has_switch_accessor or not client_node or not router_node:
            logging.debug("Skipping switch-based path priority on non-switch topology.")
            return

        try:
            if not prio_0 == prio_1:
                self.topo.command_to(client_node, "{} link set dev {} priority {}".format(
                    Experiment.IP_BIN, self.topo_config.get_client_interface(0), prio_0))
                self.topo.command_to(router_node, "{} link set dev {} priority {}".format(
                    Experiment.IP_BIN, self.topo_config.get_router_interface_to_client_switch(0), prio_0))
                self.topo.command_to(client_node, "{} link set dev {} priority {}".format(
                    Experiment.IP_BIN, self.topo_config.get_client_interface(1), prio_1))
                self.topo.command_to(router_node, "{} link set dev {} priority {}".format(
                    Experiment.IP_BIN, self.topo_config.get_router_interface_to_client_switch(1), prio_1))

            if backup_0 > 0:
                self.topo.command_to(client_node,
                                     self.topo_config.interface_backup_command(self.topo_config.get_client_interface(0)))
                self.topo.command_to(router_node,
                                     self.topo_config.interface_backup_command(self.topo_config.get_router_interface_to_client_switch(0)))
            if backup_1 > 0:
                self.topo.command_to(client_node,
                                     self.topo_config.interface_backup_command(self.topo_config.get_client_interface(1)))
                self.topo.command_to(router_node,
                                     self.topo_config.interface_backup_command(self.topo_config.get_router_interface_to_client_switch(1)))
        except (NotImplementedError, AttributeError) as e:
            logging.debug("Switch accessor not implemented on this topology, skipping: {}".format(e))

    def run_userspace_path_manager(self):
        """
        Run user-space path managers on all clients and servers (BE-8).
        """
        upmc = self.experiment_parameter.get(ExperimentParameter.USERPMC)
        upmca = self.experiment_parameter.get(ExperimentParameter.USERPMC_ARGS)
        if self.experiment_parameter.get(ExperimentParameter.KERNELPMC) == "netlink" and upmc:
            for client in getattr(self.topo_config, 'clients', []):
                log_file = os.path.join(self.output_dir, f"upmc_{client.name}.log")
                logging.info(f"Running user-space path manager on client {client.name}")
                self.topo.command_to(client, f"{upmc} {upmca} &>{log_file} &")

        upms = self.experiment_parameter.get(ExperimentParameter.USERPMS)
        upmsa = self.experiment_parameter.get(ExperimentParameter.USERPMS_ARGS)
        if self.experiment_parameter.get(ExperimentParameter.KERNELPMS) == "netlink" and upms:
            for server in getattr(self.topo_config, 'servers', []):
                log_file = os.path.join(self.output_dir, f"upms_{server.name}.log")
                logging.info(f"Running user-space path manager on server {server.name}")
                self.topo.command_to(server, f"{upms} {upmsa} &>{log_file} &")

    def clean_userspace_path_manager(self):
        """
        Clean user-space path managers on all clients and servers (BE-8).
        """
        upmc = self.experiment_parameter.get(ExperimentParameter.USERPMC)
        if self.experiment_parameter.get(ExperimentParameter.KERNELPMC) == "netlink" and upmc:
            for client in getattr(self.topo_config, 'clients', []):
                logging.info(f"Cleaning user-space path manager on client {client.name}")
                self.topo.command_to(client, f"killall {upmc}")

        upms = self.experiment_parameter.get(ExperimentParameter.USERPMS)
        if self.experiment_parameter.get(ExperimentParameter.KERNELPMS) == "netlink" and upms:
            for server in getattr(self.topo_config, 'servers', []):
                logging.info(f"Cleaning user-space path manager on server {server.name}")
                self.topo.command_to(server, f"killall {upms}")

    def run_netem_at(self):
        if hasattr(self.topo_config, 'run_netem_at'):
            self.topo_config.run_netem_at()

    def run(self):
        raise NotImplementedError("Trying to run Experiment")

    def clean(self):
        """
        Clean experiment resources: kill tcpdumps across all nodes, restore sysctl, clean endpoints.
        Each cleanup step is isolated with individual try/except (F-5).
        """
        # 0. Gracefully terminate selective per-node pcap captures (SIGINT / SIGTERM buffer flush)
        for proc in getattr(self, 'selective_pcap_procs', []):
            try:
                pid_file = proc["pid_file"]
                if not os.path.exists(pid_file):
                    continue
                with open(pid_file, "r") as f:
                    pid_val = f.read().strip()
                if pid_val and pid_val.isdigit():
                    self.topo.command_to(proc["node"], f"kill -INT {pid_val} 2>/dev/null || kill -TERM {pid_val} 2>/dev/null || true")
            except Exception as e:
                logging.warning(f"Error terminating selective pcap on {proc.get('node_name')}: {e}")

        if getattr(self, 'selective_pcap_procs', []):
            time.sleep(1.0)

        # 1. Kill tcpdump across all hosts (clients, servers, idle) and routers (BE-8, D-13)
        for host in getattr(self.topo_config, 'all_hosts', getattr(self.topo_config, 'clients', []) + getattr(self.topo_config, 'servers', [])):
            try:
                self.topo.command_to(host, "killall tcpdump")
            except Exception as e:
                logging.warning(f"Error killing tcpdump on host {getattr(host, 'name', str(host))}: {e}")

        for router in getattr(self.topo_config, 'routers', []):
            try:
                self.topo.command_to(router, "killall tcpdump")
            except Exception as e:
                logging.warning(f"Error killing tcpdump on router {router.name}: {e}")

        # 2. Restore sysctl values with isolated key error handling (BE-1, F-5)
        try:
            self.restore_sysctl()
        except Exception as e:
            logging.warning(f"Error during restore_sysctl: {e}")

        # 3. Clean MPTCP endpoints & limits for upstream kernels (BE-2b, F-5)
        if self.mptcp_generation == MptcpGeneration.UPSTREAM:
            try:
                self.clean_mptcp_endpoints()
            except Exception as e:
                logging.warning(f"Error during clean_mptcp_endpoints: {e}")

        # 4. Clean userspace PMs (BE-8, F-5)
        try:
            self.clean_userspace_path_manager()
        except Exception as e:
            logging.warning(f"Error during clean_userspace_path_manager: {e}")

        # 5. Persist unapplied parameters metadata (F-5)
        try:
            self.save_metadata()
        except Exception as e:
            logging.warning(f"Error during save_metadata: {e}")

    def setup_sysctl(self):
        """
        Record current sysctls of host and all nodes, then write experiment sysctls.
        """
        self.mptcp_generation = self.detect_mptcp_generation()
        logging.info(f"Detected MPTCP generation: {self.mptcp_generation}")
        self.save_sysctl()
        self.write_sysctl()

    def get_host_endpoint_specs(self, host):
        """
        Discover MPTCP endpoint tuples (interface_name, ip_address, is_backup) for a host.
        Supports both NetModel (GenericConfig) and MultiInterface topologies (F-1, F-3).
        """
        h_name = host.name if hasattr(host, 'name') else str(host)
        specs = []
        registered_ips = set()

        net_model = getattr(self.topo_config, 'net_model', None) or getattr(self.topo, 'net_model', None)

        # 1. NetModel-based Topology (GenericConfig)
        if net_model and h_name in net_model.hosts:
            host_spec = net_model.hosts[h_name]
            intf_names = self.topo.get_interface_names(host) if hasattr(self.topo, 'get_interface_names') else []

            # Correlate specific link with interface (F-3)
            for idx, l in enumerate(net_model.links):
                if l.a == h_name or l.b == h_name:
                    intf = getattr(self.topo, 'iface_of', {}).get((h_name, idx))
                    if not intf and intf_names:
                        intf = intf_names[0]
                    if intf and host_spec.ip:
                        ip_str = str(host_spec.ip)
                        if ip_str not in registered_ips:
                            is_backup = bool(l.backup > 0)
                            specs.append((intf, ip_str, is_backup))
                            registered_ips.add(ip_str)
                        elif len(intf_names) > 1:
                            logging.warning(f"Host {h_name} has multiple interfaces but single NetModel IP {ip_str}.")
                            self.unapplied_parameters.append({
                                "param": "mptcp_endpoints",
                                "host": h_name,
                                "interface": intf,
                                "reason": f"Duplicate IP {ip_str} on multi-interface NetModel host"
                            })

        # 2. MultiInterface / TopoConfig Accessors (MultiInterfaceTopo, MultiInterfaceMultiClients)
        elif hasattr(self.topo_config, 'clients') or hasattr(self.topo_config, 'get_client_interface'):
            is_client = host in getattr(self.topo_config, 'clients', [])
            is_server = host in getattr(self.topo_config, 'servers', [])

            if is_client:
                client_idx = self.topo_config.clients.index(host) if hasattr(self.topo_config, 'clients') and host in self.topo_config.clients else 0
                n_ifaces = self.topo_config.client_interface_count() if hasattr(self.topo_config, 'client_interface_count') else 1
                c2r_links = getattr(self.topo, 'get_client_to_router_links', lambda: [])()

                for i in range(n_ifaces):
                    intf = self.topo_config.get_client_interface(client_idx, i) if hasattr(self.topo_config, 'get_client_interface') else f"{h_name}-eth{i}"
                    ip_str = self.topo_config.get_client_ip(i, client_idx) if hasattr(self.topo_config, 'get_client_ip') else None

                    is_backup = False
                    if i < len(c2r_links) and hasattr(c2r_links[i], 'backup') and c2r_links[i].backup > 0:
                        is_backup = True
                    else:
                        backup_param = self.experiment_parameter.get(f"backup_path_{i}") if hasattr(self.experiment_parameter, 'get') else "0"
                        if backup_param and int(backup_param) > 0:
                            is_backup = True

                    if ip_str and ip_str not in registered_ips:
                        specs.append((intf, ip_str, is_backup))
                        registered_ips.add(ip_str)

            elif is_server:
                server_idx = self.topo_config.servers.index(host) if hasattr(self.topo_config, 'servers') and host in self.topo_config.servers else 0
                n_ifaces = self.topo_config.server_interface_count() if hasattr(self.topo_config, 'server_interface_count') else 1
                r2s_links = getattr(self.topo, 'get_router_to_server_links', lambda: [])()

                for j in range(n_ifaces):
                    intf = self.topo_config.get_server_interface(server_idx, j) if hasattr(self.topo_config, 'get_server_interface') else f"{h_name}-eth{j}"
                    ip_str = self.topo_config.get_server_ip(j, server_idx) if hasattr(self.topo_config, 'get_server_ip') else None

                    is_backup = False
                    if j < len(r2s_links) and hasattr(r2s_links[j], 'backup') and r2s_links[j].backup > 0:
                        is_backup = True

                    if ip_str and ip_str not in registered_ips:
                        specs.append((intf, ip_str, is_backup))
                        registered_ips.add(ip_str)

        # 3. If no endpoints resolved, warn and record unapplied parameter (F-1)
        if not specs:
            logging.warning(f"Unable to resolve MPTCP endpoints for host {h_name}")
            self.unapplied_parameters.append({
                "param": "mptcp_endpoints",
                "host": h_name,
                "reason": "Unable to resolve interfaces or IP addresses from topology configuration"
            })

        return specs

    def setup_mptcp(self):
        """
        Inject ip mptcp limits and endpoints on all host nodes for upstream kernels (BE-2b, BE-14, F-1, F-3).
        """
        if self.mptcp_generation != MptcpGeneration.UPSTREAM:
            return

        clients = getattr(self.topo_config, 'clients', [])
        servers = getattr(self.topo_config, 'servers', [])
        all_hosts = clients + servers

        for host in all_hosts:
            endpoint_specs = self.get_host_endpoint_specs(host)
            # Subflow limit derivation: minimum 2 subflows needed for MPTCP multipath (F-7)
            num_subflows = max(2, len(endpoint_specs))

            # 1. Set MPTCP limits
            self.topo.command_to(host, f"ip mptcp limits set subflow {num_subflows} add_addr_accepted {num_subflows}")

            # 2. Register endpoints with role-aware subflow / signal flags (WF-18 / T-11b)
            is_server = host in servers
            for idx, (intf, ip_addr, is_backup) in enumerate(endpoint_specs):
                backup_flag = " backup" if is_backup else ""
                if is_server:
                    if idx == 0:
                        if is_backup:
                            cmd = f"ip mptcp endpoint add {ip_addr} dev {intf} subflow backup"
                            self.topo.command_to(host, cmd)
                        continue
                    flag = "signal"
                else:
                    flag = "subflow"

                cmd = f"ip mptcp endpoint add {ip_addr} dev {intf} {flag}{backup_flag}"
                self.topo.command_to(host, cmd)

    def clean_mptcp_endpoints(self):
        """
        Flush MPTCP endpoints and reset limits on all host nodes (BE-2b).
        """
        all_hosts = getattr(self.topo_config, 'clients', []) + getattr(self.topo_config, 'servers', [])
        for host in all_hosts:
            try:
                self.topo.command_to(host, "ip mptcp endpoint flush")
                self.topo.command_to(host, "ip mptcp limits set subflow 0 add_addr_accepted 0")
            except Exception as e:
                logging.warning(f"Error cleaning MPTCP endpoints on host {host}: {e}")

    def save_sysctl(self):
        """
        Record current sysctls across host and all client/server nodes (BE-8, F-2).
        """
        self.sysctl_to_restore = {}
        self._save_sysctl(ExperimentParameter.SYSCTL_KEY, self.sysctl_to_restore, ns=False)

        self.node_sysctl_to_restore = {}
        all_hosts = getattr(self.topo_config, 'clients', []) + getattr(self.topo_config, 'servers', [])

        for node in all_hosts:
            node_name = node.name if hasattr(node, 'name') else str(node)
            self.node_sysctl_to_restore[node_name] = {}
            is_client = node in getattr(self.topo_config, 'clients', [])
            is_server = node in getattr(self.topo_config, 'servers', [])
            if not (is_client or is_server):
                continue

            if self.mptcp_generation == MptcpGeneration.LEGACY_V09X:
                dict_to_use = ExperimentParameter.SYSCTL_KEY_CLIENT if is_client else ExperimentParameter.SYSCTL_KEY_SERVER
                self._save_sysctl(dict_to_use, self.node_sysctl_to_restore[node_name], ns=True, who=node)
            elif self.mptcp_generation == MptcpGeneration.UPSTREAM:
                # Use KERNELPMC for clients, KERNELPMS for servers to unify save/write vocabulary (F-2)
                pm_key = ExperimentParameter.KERNELPMC if is_client else ExperimentParameter.KERNELPMS
                upstream_keys = {
                    pm_key: "net.mptcp.path_manager",
                    ExperimentParameter.SCHED: "net.mptcp.scheduler",
                }
                self._save_sysctl(upstream_keys, self.node_sysctl_to_restore[node_name], ns=True, who=node)

    def _save_sysctl(self, sysctl_dict, restore_target_dict, ns=False, who=None):
        for k, sysctl_key in sysctl_dict.items():
            cmd = self.read_sysctl_cmd(sysctl_key)
            val = self.topo.command_to(who, cmd) if ns else self.topo.command_global(cmd)

            if val is None or "No such file" in str(val):
                logging.warning(f"Unable to read sysctl {sysctl_key} (ns={ns}, who={who})")
                self.unapplied_parameters.append({
                    "param": k,
                    "sysctl_key": sysctl_key,
                    "reason": f"Key not found or read error on target (who={who})"
                })
            else:
                if isinstance(val, bytes):
                    val = val.decode('utf-8', errors='replace')
                val = str(val).strip()
                if "=" in val:
                    val = val.split("=", 1)[1].strip()
                restore_target_dict[k] = val

    def read_sysctl_cmd(self, key):
        return f"sysctl {key}"

    def cmd_write_sysctl(self, key, value):
        return f'sysctl -w {key}="{value}"'

    def write_sysctl(self):
        """
        Write experiment sysctls across host and all client/server nodes (BE-8, BE-2a, F-2).
        """
        self._write_sysctl(ExperimentParameter.SYSCTL_KEY, self.sysctl_to_restore, ns=False)

        all_hosts = getattr(self.topo_config, 'clients', []) + getattr(self.topo_config, 'servers', [])
        for node in all_hosts:
            node_name = node.name if hasattr(node, 'name') else str(node)
            saved_for_node = self.node_sysctl_to_restore.get(node_name, {})
            is_client = node in getattr(self.topo_config, 'clients', [])
            is_server = node in getattr(self.topo_config, 'servers', [])
            if not (is_client or is_server):
                continue

            if self.mptcp_generation == MptcpGeneration.LEGACY_V09X:
                dict_to_use = ExperimentParameter.SYSCTL_KEY_CLIENT if is_client else ExperimentParameter.SYSCTL_KEY_SERVER
                self._write_sysctl(dict_to_use, saved_for_node, ns=True, who=node)
            elif self.mptcp_generation == MptcpGeneration.UPSTREAM:
                self._write_upstream_sysctl(node, saved_for_node)

    def _write_upstream_sysctl(self, node, saved_keys):
        """
        Map legacy parameters (fullmesh, etc.) to upstream MPTCP sysctls for both clients and servers (BE-2a, BE-15, F-2).
        """
        is_client = node in getattr(self.topo_config, 'clients', [])
        is_server = node in getattr(self.topo_config, 'servers', [])
        if not (is_client or is_server):
            return
        kpm_key = ExperimentParameter.KERNELPMC if is_client else ExperimentParameter.KERNELPMS
        kpm_val = self.experiment_parameter.get(kpm_key)

        # 1. Map and write Path Manager (fullmesh/default -> kernel, netlink -> userspace) (F-2)
        if kpm_key in saved_keys:
            if kpm_val in ("fullmesh", "default"):
                mapped_pm = "kernel"
            elif kpm_val == "netlink":
                mapped_pm = "userspace"
            else:
                mapped_pm = kpm_val

            cmd = self.cmd_write_sysctl("net.mptcp.path_manager", mapped_pm)
            self.topo.command_to(node, cmd)

        # 2. Write Scheduler if saved (F-2)
        if ExperimentParameter.SCHED in saved_keys:
            sched_val = self.experiment_parameter.get(ExperimentParameter.SCHED)
            if sched_val:
                cmd = self.cmd_write_sysctl("net.mptcp.scheduler", sched_val)
                self.topo.command_to(node, cmd)

        # 3. Check and apply Congestion Control support (O2)
        cc_val = self.experiment_parameter.get(ExperimentParameter.CC)
        if cc_val:
            avail_cc = self.topo.command_to(node, "sysctl -n net.ipv4.tcp_available_congestion_control")
            if avail_cc and cc_val in str(avail_cc):
                cmd = self.cmd_write_sysctl("net.ipv4.tcp_congestion_control", cc_val)
                self.topo.command_to(node, cmd)
            elif avail_cc and cc_val not in str(avail_cc):
                logging.warning(f"Congestion control '{cc_val}' is not available in kernel (available: {str(avail_cc).strip()})")
                self.unapplied_parameters.append({
                    "param": "congctrl",
                    "value": cc_val,
                    "reason": f"Not supported in kernel tcp_available_congestion_control ({str(avail_cc).strip()})"
                })

    def _write_sysctl(self, sysctl_dict, saved_keys_dict, ns=False, who=None):
        for k in saved_keys_dict:
            sysctl_key = sysctl_dict.get(k)
            if not sysctl_key:
                continue
            sysctl_value = self.experiment_parameter.get(k)
            cmd = self.cmd_write_sysctl(sysctl_key, sysctl_value)
            val = self.topo.command_to(who, cmd) if ns else self.topo.command_global(cmd)
            if val is None:
                logging.warning(f"Unable to set sysctl {sysctl_key}={sysctl_value} (ns={ns}, who={who})")

    def restore_sysctl(self):
        """
        Restore sysctls with individual key isolation and audit logging (BE-1, F-2, F-6).
        """
        restore_log_path = os.path.join(self.output_dir, "sysctl-restore.log")
        log_lines = [f"# Sysctl Restore Audit Log - {datetime.datetime.now().isoformat()}\n"]

        # Restore global sysctls
        for k, val in self.sysctl_to_restore.items():
            sysctl_key = ExperimentParameter.SYSCTL_KEY.get(k)
            if sysctl_key:
                cmd = self.cmd_write_sysctl(sysctl_key, val)
                try:
                    res = self.topo.command_global(cmd)
                    status = "OK" if res is not None else "FAILED"
                    log_lines.append(f"GLOBAL {sysctl_key}={val} -> {status}\n")
                except Exception as e:
                    log_lines.append(f"GLOBAL {sysctl_key}={val} -> EXCEPTION: {e}\n")

        # Restore node sysctls
        all_hosts = getattr(self.topo_config, 'clients', []) + getattr(self.topo_config, 'servers', [])
        for node in all_hosts:
            node_name = node.name if hasattr(node, 'name') else str(node)
            saved_for_node = self.node_sysctl_to_restore.get(node_name, {})
            for k, val in saved_for_node.items():
                if self.mptcp_generation == MptcpGeneration.UPSTREAM:
                    key_map = {
                        ExperimentParameter.KERNELPMC: "net.mptcp.path_manager",
                        ExperimentParameter.KERNELPMS: "net.mptcp.path_manager",
                        ExperimentParameter.SCHED: "net.mptcp.scheduler",
                    }
                    sysctl_key = key_map.get(k, k)
                else:
                    is_client = node in getattr(self.topo_config, 'clients', [])
                    is_server = node in getattr(self.topo_config, 'servers', [])
                    if is_client:
                        dict_to_use = ExperimentParameter.SYSCTL_KEY_CLIENT
                    elif is_server:
                        dict_to_use = ExperimentParameter.SYSCTL_KEY_SERVER
                    else:
                        dict_to_use = {}
                    sysctl_key = dict_to_use.get(k, k)

                cmd = self.cmd_write_sysctl(sysctl_key, val)
                try:
                    res = self.topo.command_to(node, cmd)
                    status = "OK" if res is not None else "FAILED"
                    log_lines.append(f"NODE({node_name}) {sysctl_key}={val} -> {status}\n")
                except Exception as e:
                    log_lines.append(f"NODE({node_name}) {sysctl_key}={val} -> EXCEPTION: {e}\n")

        try:
            with open(restore_log_path, "w") as f:
                f.writelines(log_lines)
        except Exception as e:
            logging.warning(f"Unable to write sysctl restore log: {e}")

    def save_metadata(self):
        """
        Save experiment execution metadata including unapplied parameters (BE-2a).
        """
        metadata = {
            "timestamp": datetime.datetime.now().isoformat(),
            "output_dir": self.output_dir,
            "mptcp_generation": self.mptcp_generation,
            "unapplied_parameters": self.unapplied_parameters,
        }
        meta_path = os.path.join(self.output_dir, "metadata.json")
        try:
            with open(meta_path, "w") as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            logging.warning(f"Unable to save metadata.json: {e}")

    def run_tcpdump(self):
        client_pcap = self.experiment_parameter.get(ExperimentParameter.CLIENT_PCAP)
        server_pcap = self.experiment_parameter.get(ExperimentParameter.SERVER_PCAP)
        snaplen_pcap = self.experiment_parameter.get(ExperimentParameter.SNAPLEN_PCAP)

        for router in getattr(self.topo_config, 'routers', []):
            r_pcap_path = os.path.join(self.output_dir, f"router_{router.name}.pcap")
            self.topo.command_to(router, f"tcpdump -i any -s {snaplen_pcap} -w {r_pcap_path} &")

        if client_pcap == "yes":
            for i, client in enumerate(getattr(self.topo_config, 'clients', [])):
                c_pcap_path = os.path.join(self.output_dir, f"client-{i}.pcap")
                self.topo.command_to(client, f"tcpdump -i any -s {snaplen_pcap} -w {c_pcap_path} &")

        if server_pcap == "yes":
            for i, server in enumerate(getattr(self.topo_config, 'servers', [])):
                s_pcap_path = os.path.join(self.output_dir, f"server-{i}.pcap")
                self.topo.command_to(server, f"tcpdump -i any -s {snaplen_pcap} -w {s_pcap_path} &")

        if server_pcap == "yes" or client_pcap == "yes":
            logging.info("Activating tcpdump, waiting for it to run")
            if getattr(self.topo_config, 'client', None):
                self.topo.command_to(self.topo_config.client, "sleep 5")

    def get_pcap_node_names(self):
        raw = self.experiment_parameter.get(ExperimentParameter.PCAP_NODES) or ""
        seen = set()
        result = []
        for n in str(raw).split(","):
            name = n.strip()
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return result

    def _global_pcap_covered_nodes(self):
        """Node names already captured by the legacy global pcap/clientPcap/serverPcap flags
        (core/experiment.py run_tcpdump, BE-8). Used to avoid spawning a duplicate tcpdump
        process on the same node/interface, which would double-count S6 packet/byte totals
        and corrupt `cli.py merge` output (Challenge 8c2c7071 §2.1)."""
        covered = set()
        if self.experiment_parameter.get(ExperimentParameter.PCAP) != 'yes':
            return covered
        # Routers are captured unconditionally whenever PCAP == 'yes' (core/experiment.py:678-680)
        for router in getattr(self.topo_config, 'routers', []):
            covered.add(getattr(router, 'name', str(router)))
        if self.experiment_parameter.get(ExperimentParameter.CLIENT_PCAP) == 'yes':
            for client in getattr(self.topo_config, 'clients', []):
                covered.add(getattr(client, 'name', str(client)))
        if self.experiment_parameter.get(ExperimentParameter.SERVER_PCAP) == 'yes':
            for server in getattr(self.topo_config, 'servers', []):
                covered.add(getattr(server, 'name', str(server)))
        return covered

    def run_selective_pcap(self):
        node_names = self.get_pcap_node_names()
        if not node_names:
            return

        covered = self._global_pcap_covered_nodes()
        snaplen_pcap = self.experiment_parameter.get(ExperimentParameter.SNAPLEN_PCAP)
        self.selective_pcap_procs = []

        for name in node_names:
            if name in covered:
                logging.warning(
                    f"pcap_nodes: node '{name}' is already captured via the global pcap/clientPcap/serverPcap "
                    f"flags; skipping duplicate tcpdump to avoid double-counted S6 metrics (Challenge 8c2c7071)."
                )
                continue
            try:
                node = self.topo.get_host(name)
            except Exception:
                node = None
            if node is None:
                logging.warning(f"pcap_nodes: node '{name}' not found in topology; skipping.")
                continue
            pcap_path = os.path.join(self.output_dir, f"{name}.pcap")
            pid_file = os.path.join(self.output_dir, f".pcap-{name}.pid")
            cmd = f"tcpdump -i any -s {snaplen_pcap} -w {pcap_path} > /dev/null 2>&1 & echo $! > {pid_file}"
            self.topo.command_to(node, cmd)
            self.selective_pcap_procs.append({
                "node": node, "node_name": name,
                "pid_file": pid_file, "pcap_path": pcap_path,
            })

        if self.selective_pcap_procs:
            logging.info(f"Selective pcap capture started on: {[p['node_name'] for p in self.selective_pcap_procs]}")
            time.sleep(PCAP_WARMUP_SEC)

    def ping(self):
        client_node = getattr(self.topo_config, 'client', None)
        if client_node is None:
            if hasattr(self.topo_config, 'clients') and self.topo_config.clients:
                client_node = self.topo_config.clients[0]
            elif hasattr(self.topo, 'clients') and self.topo.clients:
                client_node = self.topo.clients[0]

        if not client_node:
            logging.warning("No client node found for ping test.")
            return

        self.topo.command_to(client_node, "rm -f {}".format(self.ping_output))
        count = self.experiment_parameter.get(ExperimentParameter.PING_COUNT)
        for j in range(0, self.topo_config.server_interface_count()):
            for i in range(0, self.topo_config.client_interface_count()):
                cmd = self.ping_command(self.topo_config.get_client_ip(i),
                                        self.topo_config.get_server_ip(interface_index=j), n=count)
                logging.info(cmd)
                self.topo.command_to(client_node, cmd)

    def ping_command(self, from_ip, to_ip, n=5):
        return "ping -c {} -I {} {} >> {}".format(n, from_ip, to_ip, self.ping_output)


class RandomFileParameter(ExperimentParameter):
    FILE = "file"
    RANDOM_SIZE = "file_size"

    def __init__(self, experiment_parameter_filename):
        super(RandomFileParameter, self).__init__(experiment_parameter_filename)
        self.default_parameters.update({
            RandomFileParameter.FILE: "random",
            RandomFileParameter.RANDOM_SIZE: "1024",
        })


class RandomFileExperiment(Experiment):
    PARAMETER_CLASS = RandomFileParameter

    def __init__(self, experiment_parameter_filename, topo, topo_config, **kwargs):
        super(RandomFileExperiment, self).__init__(
            experiment_parameter_filename, topo, topo_config, **kwargs)
        self.load_parameters()

    def load_parameters(self):
        super(RandomFileExperiment, self).load_parameters()
        self.file = self.experiment_parameter.get(RandomFileParameter.FILE)
        self.random_size = self.experiment_parameter.get(RandomFileParameter.RANDOM_SIZE)

    def prepare(self):
        super(RandomFileExperiment, self).prepare()
        if self.file == "random" and getattr(self.topo_config, 'client', None):
            self.topo.command_to(self.topo_config.client,
                                 f"dd if=/dev/urandom of=random bs=1K count={self.random_size}")

    def clean(self):
        super(RandomFileExperiment, self).clean()
        if self.file == "random" and getattr(self.topo_config, 'client', None):
            self.topo.command_to(self.topo_config.client, "rm random*")
