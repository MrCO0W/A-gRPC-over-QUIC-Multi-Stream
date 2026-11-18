import sys

try:
    from mininet.topo import Topo as MininetTopo
    from mininet.net import Mininet
    from mininet.link import TCLink
    from mininet.node import OVSBridge
    from mininet.cli import CLI
except ImportError:
    for p in ["/usr/lib/python3/dist-packages", "/usr/local/lib/python3/dist-packages"]:
        if p not in sys.path:
            sys.path.append(p)
    try:
        from mininet.topo import Topo as MininetTopo
        from mininet.net import Mininet
        from mininet.link import TCLink
        from mininet.node import OVSBridge
        from mininet.cli import CLI
    except ImportError:
        class MininetTopo:
            def __init__(self, *args, **kwargs):
                pass
            def addHost(self, name, *args, **kwargs):
                return name
            def addSwitch(self, name, *args, **kwargs):
                return name
            def addLink(self, *args, **kwargs):
                return None
        Mininet = None
        TCLink = None
        OVSBridge = None
        CLI = None

from subprocess import Popen, PIPE
import logging

class MininetBuilder(MininetTopo):
    def __init__(self):
        super().__init__()
        self.net = None

    def command_to(self, who, cmd):
        """
        Launch command `cmd` to the specific name space of `who`
        """
        return who.cmd(cmd)

    def command_global(self, cmd):
        """
        Launch command `cmd` over the global system, i.e., not specific to a name space
        """
        p = Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE)
        stdout, stderr = p.communicate()
        if p.returncode != 0:
            err_msg = stderr.decode('utf-8', errors='replace').strip() if isinstance(stderr, bytes) else str(stderr).strip()
            logging.error("Got error (rc={}) when running cmd: '{}', stderr: '{}'".format(p.returncode, cmd, err_msg))
            return None
        return stdout.decode('utf-8', errors='replace') if isinstance(stdout, bytes) else stdout

    def start_network(self):
        """
        Start the network using Mininet

        Note that the use of OVSBridge avoid facing issues with OVS controllers.
        """
        self.net = Mininet(topo=self, link=TCLink, switch=OVSBridge, controller=None)
        self.net.start()

    def get_cli(self):
        """
        Get the Mininet command line interface
        """
        if self.net is None:
            logging.error("Cannot get the CLI")
        else:
            CLI(self.net)

    def get_host(self, who):
        if self.net is None:
            logging.error("Network not available...")
            raise Exception("Network not ready")
        else:
            return self.net.getNodeByName(who)

    def get_interface_names(self, who):
        # NOTE: bs1.intfNames()[0] is lo...
        return [i for i in who.intfNames() if i != "lo"]

    def add_host(self, host):
        return self.addHost(host)

    def add_switch(self, switch):
        return self.addSwitch(switch)

    def add_link(self, from_a, to_b, **kwargs):
        return self.addLink(from_a, to_b, **kwargs)

    def stop_network(self):
        if self.net is None:
            logging.warning("Unable to stop the network: net is None")
        else:
            self.net.stop()
