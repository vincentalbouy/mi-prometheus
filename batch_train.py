"""
This scripts does a random search on DNC's hyper parameters.
It works by loading a template yaml file, modifying the resulting dict, and dumping that as yaml into a
temporary file. The `train.py` script is then launched using the temporary yaml file as the task.
It will run as many concurrent jobs as possible.
"""

import os
import yaml
from random import randrange
from itertools import repeat
from tempfile import NamedTemporaryFile
from multiprocessing.pool import ThreadPool
import subprocess


EXPERIMENT_REPETITIONS = 10


def main():
    # Load the list of yaml files to run
    yaml_files = []
    with open("CHANGE THIS THING", 'r') as f:
        for line in f.readline():
            assert os.path.isfile(line), line + " is not a file"
            yaml_files.append(line)

    experiments_list = []
    for elem in yaml_files:
        experiments_list.extend(repeat(elem, EXPERIMENT_REPETITIONS))

    # Run in as many threads as there are CPUs available to the script
    with ThreadPool(processes=len(os.sched_getaffinity(0))) as pool:
        pool.map(run_experiment, experiments_list)


def run_experiment(yaml_file_path: str):
    # Load template yaml file
    with open(yaml_file_path, 'r') as yaml_file:
        params = yaml.load(yaml_file)

    # Change some params to random ones with specified ranges
    params['settings']['numpy_seed'] = randrange(0, 2**32)
    params['settings']['torch_seed'] = randrange(0, 2**32)

    # Create temporary file, in which we dump the modified params dict as yaml
    with NamedTemporaryFile(mode='w') as temp_yaml:
        yaml.dump(params, temp_yaml, default_flow_style=False)

        command_str = "cuda-gpupick -n0 python3 train.py -t {0} --tensorboard 0".format(temp_yaml.name).split()

        with open(os.devnull, 'w') as devnull:
            result = subprocess.run(command_str, stdout=devnull)

        if result.returncode != 0:
            print("Training exited with code:", result.returncode)


if __name__ == '__main__':
    main()
