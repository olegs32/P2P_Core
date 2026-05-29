# # GRID/services/compute/service.py
# import asyncio
#
# from GRID.base import ModuleGeneric
# from GRID.services.rpc import rpc, stream_wrapper, stream_consumer
# from GRID.memory import Pipe
#
#
# class Compute(ModuleGeneric):
#     def __init__(self, name, context):
#         super().__init__(name, context)
#
#     @rpc
#     def status(self, data):
#         return 'ok'
#
#     @stream_wrapper('run_range')
#     async def prepare_run(self, data: dict):
#         """Подготовка: инициализация, аллокация ресурсов."""
#         self.log.info(f'preparing for stream, data={data}')
#         ctx = {
#             'results': [],
#             'multiplier': data.get('multiplier', 1),
#         }
#         return ctx  # передаётся в consumer
#
#     @stream_consumer('run_range')
#     async def consume_ranges(self, pipe: Pipe, ctx: dict):
#         """Основной цикл потребления чанков."""
#         async for chunk in pipe:
#             result = chunk[0] * ctx['multiplier']
#             ctx['results'].append(result)
#             self.log.debug(f'processed chunk: {chunk} → {result}')
#             await asyncio.sleep(1)
#
#         self.log.info(f'stream done, total results: {len(ctx["results"])}')