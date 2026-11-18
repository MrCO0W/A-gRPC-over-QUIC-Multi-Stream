from core.experiment import ExperimentParameter, RandomFileExperiment, RandomFileParameter
import time
import os

class IperfExp(RandomFileExperiment):
    # APPNAME = "qcoap-uni"
    EXP = "simple"
    EXPTYPE = "iperf_exp"
    NAME = "iperf_exp"
    SERVER_0_LOG = os.path.join("output", "server-0-log.txt")
    SERVER_1_LOG = os.path.join("output", "server-1-log.txt")
    CLIENT_0_LOG = os.path.join("output", "client-0-log.txt")
    CLIENT_1_LOG = os.path.join("output", "client-1-log.txt")
    PING_OUTPUT = os.path.join("output", "ping.log")

    def __init__(self, experiment_parameter_filename, topo, topo_config, **kwargs):
        super(IperfExp, self).__init__(experiment_parameter_filename, topo, topo_config, **kwargs)

        self.EXP = self.experiment_parameter.get("exp")

    def load_parameters(self):
        super(IperfExp, self).load_parameters()

    def prepare(self):
        super(IperfExp, self).prepare()
        for i, client in enumerate(self.topo_config.clients):
            self.topo.command_to(client, f'rm -f {os.path.join(self.output_dir, f"client-{i}-log.txt")}')
        for i, server in enumerate(self.topo_config.servers):
            self.topo.command_to(server, f'rm -f {os.path.join(self.output_dir, f"server-{i}-log.txt")}')

    def clean(self):
        super(IperfExp, self).clean()

    def checkNetwork(self, entity):
        self.topo.command_to(entity, f"route -n > {os.path.join(self.output_dir, 'route.out')}")
        self.topo.command_to(entity, f"ip addr >> {os.path.join(self.output_dir, 'route.out')}")

    def getServerCmd(self, log):
        return f'iperf -s >> {log} &'

    def getClientCmd(self, dest, log):
        return f"iperf -c {dest} -t 10 >> {log}"

    def run(self):
        num_servers = len(self.topo_config.servers)
        num_clients = len(self.topo_config.clients)
        if num_servers == 0 or num_clients == 0:
            return

        for i, server in enumerate(self.topo_config.servers):
            log_path = os.path.join(self.output_dir, f"server-{i}-log.txt")
            self.topo.command_to(
                server,
                self.getServerCmd(log_path),
            )

        time.sleep(1)

        for i, client in enumerate(self.topo_config.clients):
            server_idx = i % num_servers
            dest_ip = self.topo_config.get_server_ip(0, server_idx)
            log_path = os.path.join(self.output_dir, f"client-{i}-log.txt")
            self.topo.command_to(
                client,
                self.getClientCmd(dest_ip, log_path),
            )


