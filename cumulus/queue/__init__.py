from jsonpath_rw import parse

from . import sge
from cumulus.constants import QueueType

type_to_adapter = {
    QueueType.SGE: sge.SgeQueueAdapter,
}


def get_queue_adapter(cluster):
    global type_to_adapter

    system = parse('queue.system').find(cluster)
    if system:
        system = system[0].value
    # Default to SGE
    else:
        system = QueueType.SGE

    if system not in type_to_adapter:
        raise Exception('Unsupported queuing system: %s' % system)

    return type_to_adapter[system]
