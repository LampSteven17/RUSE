import argparse
import signal
import os
import random
import sys
from importlib import import_module
from time import sleep


TASK_CLUSTER_COUNT = 5
TASK_INTERVAL_SECONDS = 10
GROUPING_INTERVAL_SECONDS = 500
EXTRA_DEFAULTS = []


def emulation_loop(workflows, clustersize, taskinterval, taskgroupinterval, extra,
                   clustersize_sigma=0.0, taskinterval_sigma=0.0):
    while True:
        # D5: Jitter clustersize per cluster via lognormal noise
        if clustersize_sigma > 0:
            effective_cs = max(2, int(clustersize * random.lognormvariate(0, clustersize_sigma)))
        else:
            effective_cs = clustersize

        for c in range(effective_cs):
            # D5: Jitter taskinterval per task via lognormal noise
            if taskinterval_sigma > 0:
                effective_ti = max(1, int(taskinterval * random.lognormvariate(0, taskinterval_sigma)))
            else:
                effective_ti = taskinterval
            sleep(random.randrange(max(1, effective_ti)))
            index = random.randrange(len(workflows))
            print(workflows[index].display)
            workflows[index].action(extra)
        sleep(random.randrange(taskgroupinterval))


def import_workflows():
    extensions = []
    for root, dirs, files in os.walk(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'app', 'workflows')):
        files = [f for f in files if not f[0] == '.' and not f[0] == "_"]
        dirs[:] = [d for d in dirs if not d[0] == '.' and not d[0] == "_"]
        for file in files:
            try:
                extensions.append(load_module('app/workflows', file))
            except Exception as e:
                print('Error could not load workflow. {}'.format(e))
    return extensions


def load_module(root, file):
    module = os.path.join(*root.split('/'), file.split('.')[0]).replace(os.path.sep, '.')
    workflow_module = import_module(module)
    return getattr(workflow_module, 'load')()


def run(clustersize, taskinterval, taskgroupinterval, extra, seed=42,
        clustersize_sigma=0.0, taskinterval_sigma=0.0):
    if seed != 0:
        random.seed(seed)
    else:
        random.seed()
    workflows = import_workflows()

    def signal_handler(sig, frame):
        for workflow in workflows:
            workflow.cleanup()
        exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if clustersize_sigma > 0:
        print(f"D5: clustersize jitter enabled (sigma={clustersize_sigma})")
    else:
        print("[WARNING] D5 clustersize_sigma DISABLED — no --clustersize-sigma provided")
    if taskinterval_sigma > 0:
        print(f"D5: taskinterval jitter enabled (sigma={taskinterval_sigma})")
    else:
        print("[WARNING] D5 taskinterval_sigma DISABLED — no --taskinterval-sigma provided")

    emulation_loop(workflows=workflows, clustersize=clustersize, taskinterval=taskinterval,
                    taskgroupinterval=taskgroupinterval, extra=extra,
                    clustersize_sigma=clustersize_sigma, taskinterval_sigma=taskinterval_sigma)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Emulate human behavior on a system')
    parser.add_argument('--clustersize', type=int, default=TASK_CLUSTER_COUNT)
    parser.add_argument('--taskinterval', type=int, default=TASK_INTERVAL_SECONDS)
    parser.add_argument('--taskgroupinterval', type=int, default=GROUPING_INTERVAL_SECONDS)
    parser.add_argument('--extra', nargs='*', default=EXTRA_DEFAULTS)
    parser.add_argument('--seed', type=int, default=42,
                        help='Random seed for deterministic behavior (default: 42, 0 = non-deterministic)')
    parser.add_argument('--clustersize-sigma', type=float, default=0.0,
                        help='Lognormal sigma for clustersize jitter (0=exact, D5)')
    parser.add_argument('--taskinterval-sigma', type=float, default=0.0,
                        help='Lognormal sigma for taskinterval jitter (0=exact, D5)')
    args = parser.parse_args()

    try:
        run(
            clustersize=args.clustersize,
            taskinterval=args.taskinterval,
            taskgroupinterval=args.taskgroupinterval,
            extra=args.extra,
            seed=args.seed,
            clustersize_sigma=args.clustersize_sigma,
            taskinterval_sigma=args.taskinterval_sigma,
        )
    except KeyboardInterrupt:
        print(" Terminating human execution...")
        sys.exit()
