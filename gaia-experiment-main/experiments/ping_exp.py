from core.experiment import ExperimentParameter, RandomFileExperiment, RandomFileParameter
import logging
import time
import os

class PingExp(RandomFileExperiment):
    # APPNAME = "qcoap-uni"
    EXP = "simple"
    DEVS = "1"
    EXPTYPE = "ping_exp"
    NAME = "ping_exp"
    CLIENT_LOG_FMT = "client-{}-log.txt"
    SERVER_LOG_FMT = "server-{}-log.txt"
    PING_OUTPUT = "ping.log"

    def __init__(self, experiment_parameter_filename, topo, topo_config, **kwargs):
        super(PingExp, self).__init__(experiment_parameter_filename, topo, topo_config, **kwargs)
        self.EXP = self.experiment_parameter.get("exp")

    def load_parameters(self):
        super(PingExp, self).load_parameters()

    # Delete the existing log files
    def prepare(self):
        super(PingExp, self).prepare()
        for i, client in enumerate(self.topo_config.clients):
            log_path = os.path.join(self.output_dir, PingExp.CLIENT_LOG_FMT.format(i))
            self.topo.command_to(client, f'rm -f {log_path}')
        for i, server in enumerate(self.topo_config.servers):
            log_path = os.path.join(self.output_dir, PingExp.SERVER_LOG_FMT.format(i))
            self.topo.command_to(server, f'rm -f {log_path}')

    def clean(self):
        super(PingExp, self).clean()

    def checkNetwork(self, entity):
        self.topo.command_to(entity, "route -n > route.out")
        self.topo.command_to(entity, "ip addr >> route.out")

    def get_ping_command(self, dest, log, count=50):
        return f'echo "=== ping {dest} ===" >> {log}; ping -c {count} {dest} >> {log}'

    def _destinations(self, get_ip, host_count, interface_count):
        """Ordered-unique destination IPs over the (interface, host) index space.

        TopoConfig subclasses disagree on which half of the (interface_index,
        host_index) pair they honour, so sweep both and de-duplicate. A list is
        used rather than a set to keep command order deterministic.
        """
        dests = []
        for interface_index in range(interface_count):
            for host_index in range(host_count):
                ip = get_ip(interface_index, host_index)
                if ip not in dests:
                    dests.append(ip)
        return dests

    def run(self):
        clients = self.topo_config.clients
        servers = self.topo_config.servers
        if not clients or not servers:
            logging.warning("PingExp: no clients or no servers, skipping ping run.")
            return

        count = self.experiment_parameter.get(ExperimentParameter.PING_COUNT)

        server_dests = self._destinations(
            self.topo_config.get_server_ip, len(servers),
            self.topo_config.server_interface_count())
        client_dests = self._destinations(
            self.topo_config.get_client_ip, len(clients),
            self.topo_config.client_interface_count())

        logging.info("Starting simultaneous pings across all clients and servers...")

        # 1. Launch background pings on all clients
        for i, client in enumerate(clients):
            log = os.path.join(self.output_dir, PingExp.CLIENT_LOG_FMT.format(i))
            for dest in server_dests:
                self.topo.command_to(client, self.get_ping_command(dest, log, count) + " &")

        # 2. Launch background pings on all servers
        for j, server in enumerate(servers):
            log = os.path.join(self.output_dir, PingExp.SERVER_LOG_FMT.format(j))
            for dest in client_dests:
                self.topo.command_to(server, self.get_ping_command(dest, log, count) + " &")

        # 3. Wait for all background ping processes to finish on all nodes
        for node in clients + servers:
            self.topo.command_to(node, "wait")
        logging.info("All simultaneous ping jobs completed.")