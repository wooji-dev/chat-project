import json
from broadcaster import Broadcast
from starlette.applications import Starlette
from starlette.concurrency import run_until_first_complete
from starlette.routing import Route, WebSocketRoute
from starlette.templating import Jinja2Templates
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os
import requests
import aiohttp
import asyncio


load_dotenv()  # Load .env variables

if os.environ.get("ENV_STATE") == "prod":
    print("Production mode")
    broadcast = Broadcast("redis://"+os.environ.get('REDIS_HOST')+":6379")
else:
    print("Development mode")
    broadcast = Broadcast("redis://127.0.0.1:6379")
templates = Jinja2Templates("templates")


async def homepage(request):
    template = "index.html"
    context = {"request": request}
    return templates.TemplateResponse(template, context)


async def chatroom_ws(websocket):
    await websocket.accept()
    channel_name = "demo"
    await run_until_first_complete(
        (chatroom_ws_receiver, {"websocket": websocket, "channel_name": channel_name}),
        (chatroom_ws_sender, {"websocket": websocket, "channel_name": channel_name}),
    )


async def chatroom_ws_receiver(websocket, channel_name):
    async for message in websocket.iter_text():
        await broadcast.publish(channel=channel_name, message=message)


async def chatroom_ws_sender(websocket, channel_name):
    async with broadcast.subscribe(channel=channel_name) as subscriber:
        async for event in subscriber:
            await websocket.send_text(
                event.message
            )
            await websocket.send_text(
                '{"action":"message","user":"안내사항","message":"봇이 입력 중입니다"}'
            )
            params = {"m": json.loads(event.message)['message']}
            async with aiohttp.ClientSession() as session:
                async with session.get("https://2lc3zydxt0.execute-api.ap-northeast-2.amazonaws.com/default/test-01", params=params) as resp:
                    r = await resp.json()
            bot_message = r['choices'][0]['message']['content']
            bot_message = json.dumps(bot_message, ensure_ascii=False)
            await websocket.send_text(
                f'{{"action":"message","user":"신한투자증권 프로봇","message":{bot_message}}}'
            )


routes = [
    Route("/", homepage),
    WebSocketRoute("/", chatroom_ws, name='chatroom_ws'),
]

origins = [
    "http://localhost",
    "http://localhost:8000"
]

middleware = [
    Middleware(CORSMiddleware,
               allow_origins=origins,
               allow_methods=['*'],
               allow_headers=['*'])
]

app = Starlette(
    routes=routes,
    on_startup=[broadcast.connect],
    on_shutdown=[broadcast.disconnect],
    middleware=middleware
)