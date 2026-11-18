from core.experiment import ExperimentParameter, RandomFileExperiment, RandomFileParameter
import logging
import time
import os


class PerformanceAnalysisExp(RandomFileExperiment):
    # APPNAME = "qcoap-uni"
    EXP = "simple"
    NC = "10"
    PS = "100"
    PROTOCOL = "udp"
    EXPTYPE = "performance-analysis"
    NAME = "performance-analysis"

    def __init__(self, experiment_parameter_filename, topo, topo_config, **kwargs):
        # Just rely on RandomFileExperiment
        super(PerformanceAnalysisExp, self).__init__(
            experiment_parameter_filename, topo, topo_config, **kwargs)

        self.EXP = self.experiment_parameter.get("exp")
        self.NC = self.experiment_parameter.get("nc")
        self.PS = self.experiment_parameter.get("ps")
        self.PROTOCOL = self.experiment_parameter.get("protocol")

    @property
    def server_log(self):
        return os.path.join(self.output_dir, "server-log.txt")

    @property
    def client_log(self):
        return os.path.join(self.output_dir, "client-log.txt")

    @property
    def gateway_log(self):
        return os.path.join(self.output_dir, "gateway-log.txt")

    def load_parameters(self):
        # Just rely on RandomFileExperiment
        super(PerformanceAnalysisExp, self).load_parameters()

    def prepare(self):
        super(PerformanceAnalysisExp, self).prepare()
        if hasattr(self.topo_config, 'clients') and self.topo_config.clients:
            for client in self.topo_config.clients:
                self.topo.command_to(client, f"rm -f {self.client_log}")
        elif hasattr(self.topo_config, 'client'):
            self.topo.command_to(self.topo_config.client, f"rm -f {self.client_log}")

    def clean(self):
        super(PerformanceAnalysisExp, self).clean()

    def checkNetwork(self, entity):
        self.topo.command_to(entity,
                             f"route -n > {os.path.join(self.output_dir, 'route.out')}")
        self.topo.command_to(entity,
                             f"ip addr > {os.path.join(self.output_dir, 'ipaddr.out')}")

    def getServerCmd(self, log):
        s = '{}/../utils/pa -p {} -nc {} -ps {} -s > {} &'.format(
            os.path.dirname(os.path.abspath(__file__)),
            self.PROTOCOL,
            self.NC,
            self.PS,
            log,
        )

        return s

    def getGatewayCmd(self, dest, log):
        s = '{}/../utils/pa -p {} -nc {} -ps {} -gw {} > {} &'.format(
            os.path.dirname(os.path.abspath(__file__)),
            self.PROTOCOL,
            self.NC,
            self.PS,
            dest,
            log,
        )

        return s

    def getClientCmd(self, dest, log):
        s = '{}/../utils/pa -p {} -nc {} -ps {} {} > {}'.format(
            os.path.dirname(os.path.abspath(__file__)),
            self.PROTOCOL,
            self.NC,
            self.PS,
            dest,
            log,
        )

        return s

    def run(self):
        self.topo.command_to(self.topo_config.router,
                             f"netstat -sn > {os.path.join(self.output_dir, 'netstat_router_before')}")

        serverCmd = self.getServerCmd(self.server_log)
        logging.info(serverCmd)

        server = self.topo_config.servers[0] if hasattr(self.topo_config, 'servers') and self.topo_config.servers else getattr(self.topo_config, 'server', None)
        client = self.topo_config.clients[0] if hasattr(self.topo_config, 'clients') and self.topo_config.clients else getattr(self.topo_config, 'client', None)

        if server is None or client is None:
            logging.warning("No server or client found for performance analysis")
            return

        dest_ip = self.topo_config.get_server_ip(0)
        clientCmd = self.getClientCmd('{}:5683'.format(dest_ip), self.client_log)
        if self.PROTOCOL == 'udpc':
            clientCmd = self.getClientCmd('{}:5684'.format(dest_ip), self.client_log)
        logging.info(clientCmd)

        gatewayCmd = self.getGatewayCmd('{}:5684'.format(dest_ip), self.gateway_log)
        logging.info(gatewayCmd)

        self.topo.command_to(server, serverCmd)
        time.sleep(1)

        if hasattr(self.topo_config, 'clients') and len(self.topo_config.clients) > 1:
            self.topo.command_to(self.topo_config.clients[1], gatewayCmd)

        for _ in range(5):
            time.sleep(1)

        self.topo.command_to(client, clientCmd)
        time.sleep(1)

        self.topo.command_to(self.topo_config.router,
                             f"netstat -sn > {os.path.join(self.output_dir, 'netstat_router_after')}")
        self.topo.command_to(server,
                             "pkill -f pa || true")
        if hasattr(self.topo_config, 'clients') and len(self.topo_config.clients) > 1:
            self.topo.command_to(self.topo_config.clients[1],
                                 "pkill -f pa || true")
