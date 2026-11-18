#!/usr/bin/python

from core.experiment import Experiment, ExperimentParameter
from core.topo import Topo, TopoParameter

from mininet_builder import MininetBuilder
try:
    from mininet.clean import cleanup
except ImportError:
    def cleanup():
        pass

from experiments import EXPERIMENTS
from topos import TOPO_CONFIGS, TOPOS

import datetime
import logging
import os
import subprocess
import sys
import traceback


def get_git_revision_short_hash():
    # Because we might run Minitopo from elsewhere.
    curr_dir = os.getcwd()
    ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
    os.chdir(ROOT_DIR)
    try:
        ret = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'], stderr=subprocess.DEVNULL).decode("utf-8").strip()
    except Exception:
        ret = "unknown"
    finally:
        os.chdir(curr_dir)
    return ret


class Runner(object):
    """
    Run an experiment described by `experiment_parameter_file` in the topology
    described by `topo_parameter_file` in the network environment built by
    `builder_type`.

    Lifecycle is decomposed into setup(), run(), and teardown() (BE-5).
    """

    def __init__(self, builder_type, topo_parameter_file, experiment_parameter_file=None, auto_run=True, output_dir=None):
        logging.info("Minitopo version {}".format(
            get_git_revision_short_hash()))
        if output_dir is None:
            timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            self.output_dir = os.path.join("output", timestamp)
        else:
            self.output_dir = output_dir

        self.builder_type = builder_type
        self.topo_parameter_file = topo_parameter_file
        self.topo_parameter = TopoParameter(topo_parameter_file)
        self.experiment_parameter_file = experiment_parameter_file
        self.topo_builder = None
        self.topo = None
        self.topo_config = None

        if auto_run:
            try:
                self.setup()
                self.run(experiment_parameter_file)
            finally:
                self.teardown()

    def setup(self):
        """
        Instantiate builder, topology, and configure the network (BE-5, WF-11).
        """
        from core.validation import validate_config
        is_valid, report = validate_config(self.topo_parameter_file, self.experiment_parameter_file)
        if not is_valid:
            err_msg = "; ".join(report.get("errors", ["Validation failed"]))
            raise ValueError(f"Topology/Experiment validation failed: {err_msg}")

        os.makedirs(self.output_dir, exist_ok=True)
        self.set_builder(self.builder_type)
        self.apply_topo()
        if hasattr(self.topo, 'set_output_dir'):
            self.topo.set_output_dir(self.output_dir)
        self.apply_topo_config()
        self.start_topo()

    def run(self, experiment_parameter_file=None):
        """
        Run the configured experiment (BE-5).
        """
        xp_file = experiment_parameter_file or self.experiment_parameter_file
        if not xp_file:
            raise ValueError("No experiment parameter file provided to run.")
        self.run_experiment(xp_file)

    def teardown(self):
        """
        Tear down network topology cleanly (BE-5).
        """
        self.stop_topo()

    def set_builder(self, builder_type):
        """
        Currently the only builder type supported is Mininet...
        """
        if builder_type == Topo.MININET_BUILDER:
            self.topo_builder = MininetBuilder()
        else:
            raise Exception(
                "Unknown builder type: {}".format(builder_type))

    def apply_topo(self):
        """
        Instantiate the topology
        """
        topo_type = self.topo_parameter.get(Topo.TOPO_ATTR)
        if topo_type in TOPOS:
            self.topo = TOPOS[topo_type](
                self.topo_builder, self.topo_parameter)
            logging.info(
                "Using topo {}".format(self.topo.__str__().strip()))
        else:
            raise Exception("Unknown topo {}".format(topo_type))

    def apply_topo_config(self):
        """
        Configure the topology
        """
        topo_type = self.topo_parameter.get(Topo.TOPO_ATTR)
        if topo_type in TOPO_CONFIGS:
            self.topo_config = TOPO_CONFIGS[topo_type](
                self.topo, self.topo_parameter)
            logging.info(
                "Using topo config {}".format(self.topo_config))
        else:
            raise Exception("Unknown topo config {}".format(topo_type))

    def start_topo(self):
        """
        Start the topology
        """
        self.topo.start_network()
        self.topo_config.configure_network()

    def run_experiment(self, experiment_parameter_file):
        """
        Match the name of the experiment and launch it
        """
        xp = ExperimentParameter(experiment_parameter_file).get(
            ExperimentParameter.XP_TYPE)
        if xp in EXPERIMENTS:
            exp = EXPERIMENTS[xp](experiment_parameter_file,
                                  self.topo, self.topo_config,
                                  output_dir=self.output_dir)
            exp.classic_run()
        else:
            raise Exception("Unknown experiment {}".format(xp))

    def stop_topo(self):
        """
        Stop the topology
        """
        if self.topo:
            self.topo.stop_network()


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Minitopo, a wrapper of Mininet to run multipath experiments")

    parser.add_argument("--topo_param_file", "-t", required=True,
                        help="path to the topo parameter file")
    parser.add_argument("--experiment_param_file", "-x", required=True,
                        help="path to the experiment parameter file")
    parser.add_argument("--output_dir", "-o", default=None,
                        help="path to output directory for logs and results")

    args = parser.parse_args(argv)

    logging.basicConfig(
        format="%(asctime)-15s [%(levelname)s] %(funcName)s: %(message)s", level=logging.INFO)

    error_occurred = False
    try:
        Runner(Topo.MININET_BUILDER, args.topo_param_file,
               args.experiment_param_file, output_dir=args.output_dir)
    except Exception as e:
        logging.fatal("A fatal error occurred: {}".format(e))
        traceback.print_exc()
        error_occurred = True
    finally:
        # Always cleanup Mininet
        logging.info("cleanup mininet")
        try:
            cleanup()
        except Exception as e:
            logging.warning("Error during Mininet cleanup: {}".format(e))

    if error_occurred:
        sys.exit(1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
