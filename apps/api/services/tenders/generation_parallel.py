"""Bounded model work; callers reduce immutable results in source order."""
import asyncio
import logging
import os
import inspect
from time import perf_counter

logger = logging.getLogger(__name__)


async def complete_json(client, messages, *, mode='pro'):
    options = {'mode': mode}
    if 'response_format' in inspect.signature(client.complete).parameters:
        options['response_format'] = {'type':'json_object'}
    return await client.complete(messages, **options)


async def ordered_generation(jobs, *, stage, labels=None):
    limit = max(1, min(16, int(os.getenv('TENDER_GENERATE_CONCURRENCY', '8'))))
    # A single event loop can run several independent pre-generation batches
    # at once. Reuse one gate per configured limit so gather() does not multiply
    # the model-side concurrency beyond the configured upper bound.
    loop = asyncio.get_running_loop()
    gates = getattr(loop, '_tender_generation_gates', None)
    if gates is None:
        gates = {}
        loop._tender_generation_gates = gates
    gate = gates.setdefault(limit, asyncio.Semaphore(limit))
    started = perf_counter()
    async def run(index, job):
        async with gate:
            tick = perf_counter()
            try:
                return await job()
            finally:
                label = labels[index-1] if labels else str(index)
                logger.info('generation stage=%s chapter=%s job=%s concurrency=%s seconds=%.3f', stage, label, index, limit, perf_counter()-tick)
    results = await asyncio.gather(*(run(i, job) for i, job in enumerate(jobs, 1)), return_exceptions=True)
    logger.info('generation stage=%s jobs=%s concurrency=%s seconds=%.3f', stage, len(jobs), limit, perf_counter()-started)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results
