"""
Declarative aiohttp HTTP route table for VOLT MUSIC microservice.
"""
import uuid
from aiohttp import web
from controllers.music_controller import MusicController


def build_routes(ctrl: MusicController) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get('/status')
    async def handle_status(request):
        return web.json_response({"connected": ctrl.is_connected, "room": ctrl.current_room})

    @routes.get('/state')
    async def handle_state(request):
        return web.json_response(ctrl.get_state())

    @routes.post('/summon')
    async def handle_summon(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        room = data.get('room', 'lounge') or 'lounge'
        await ctrl.summon(room)
        return web.json_response(ctrl.get_state())

    @routes.post('/dismiss')
    async def handle_dismiss(request):
        await ctrl.dismiss()
        return web.json_response(ctrl.get_state())

    @routes.post('/search')
    async def handle_search(request):
        data = await request.json()
        q = data.get('query', '').strip()
        results = await ctrl.resolver.search_tracks(q)
        return web.json_response({"ok": True, "results": results})

    @routes.post('/play')
    async def handle_play(request):
        data = await request.json()
        room = data.get('room') or ctrl.current_room or 'lounge'
        if not ctrl.is_live_connected(room):
            await ctrl.summon(room)
        item = data.get('item')
        if not item and data.get('query'):
            item = await ctrl.resolver.resolve_query_to_item(data['query'])
        if not item:
            return web.json_response({"ok": False, "error": "Трек не найден"})
        if 'id' not in item:
            item['id'] = uuid.uuid4().hex[:8]
        ok = await ctrl.play_item(item)
        return web.json_response({"ok": ok, **ctrl.get_state()})

    @routes.post('/queue_add')
    async def handle_queue_add(request):
        data = await request.json()
        room = data.get('room') or ctrl.current_room or 'lounge'
        if not ctrl.is_live_connected(room):
            await ctrl.summon(room)
        item = data.get('item')
        if not item and data.get('query'):
            item = await ctrl.resolver.resolve_query_to_item(data['query'])
        if not item:
            return web.json_response({"ok": False, "error": "Трек не найден"})
        if 'id' not in item:
            item['id'] = uuid.uuid4().hex[:8]
        if not ctrl.state.is_playing:
            await ctrl.play_item(item)
        else:
            ctrl.state.queue.append(item)
        return web.json_response({"ok": True, **ctrl.get_state()})

    @routes.post('/queue_remove')
    async def handle_queue_remove(request):
        data = await request.json()
        ctrl.state.remove_from_queue(data.get('id'))
        return web.json_response({"ok": True, **ctrl.get_state()})

    @routes.post('/queue_clear')
    async def handle_queue_clear(request):
        ctrl.state.queue.clear()
        return web.json_response({"ok": True, **ctrl.get_state()})

    @routes.post('/next')
    async def handle_next(request):
        await ctrl.play_next_in_queue()
        return web.json_response({"ok": True, **ctrl.get_state()})

    @routes.post('/loop')
    async def handle_loop(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        mode = ctrl.state.cycle_loop_mode(data.get('mode'))
        print(f"[MUSIC] Loop mode switched to: {mode}")
        return web.json_response({"ok": True, **ctrl.get_state()})

    @routes.post('/stop')
    async def handle_stop(request):
        await ctrl.audio_engine.stop_music()
        return web.json_response({"ok": True, **ctrl.get_state()})

    @routes.post('/eq')
    async def handle_eq(request):
        data = await request.json()
        await ctrl.update_eq(data)
        return web.json_response({"ok": True, **ctrl.get_state()})

    return routes
