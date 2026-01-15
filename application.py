import json
import os
import aiohttp

from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.routing import Route, WebSocketRoute
from starlette.templating import Jinja2Templates
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocket, WebSocketDisconnect

load_dotenv()

templates = Jinja2Templates("templates")

BOT_NAME = os.environ.get("BOT_NAME", "신한투자증권 프로봇")
BOT_API_URL = os.environ.get(
    "BOT_API_URL",
    "https://bm0l8cj2xl.execute-api.ap-northeast-2.amazonaws.com/default/llm-lamda",
)

origins = [
    "http://localhost",
    "http://localhost:8000",
]

middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
]


async def homepage(request):
    return templates.TemplateResponse("index.html", {"request": request})


def _extract_user_message(payload: dict) -> str:
    v = payload.get("message")
    if isinstance(v, str) and v.strip():
        return v.strip()
    # 혹시라도 다른 키로 보내면 흡수
    for k in ("text", "m", "userMessage"):
        vv = payload.get(k)
        if isinstance(vv, str) and vv.strip():
            return vv.strip()
    return ""


async def call_bot_api_raw(user_text: str) -> str:
    """
    봇 API(Lambda/API Gateway) 응답을 '원문 그대로' 문자열로 반환한다.
    예:
    {
      "statusCode": 200,
      "headers": {...},
      "body": "{\"answer\":\"...\"}"
    }
    이런 텍스트를 그대로 반환.
    """
    params = {"m": user_text}
    timeout = aiohttp.ClientTimeout(total=45)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(BOT_API_URL, params=params) as resp:
            # status가 4xx/5xx여도 "원문"이 중요하면 text 그대로 가져온다.
            raw_text = await resp.text()

            # 그래도 status 정보를 시스템이 알 수 있게 하고 싶으면, 여기서 에러로 보내지 말고 raw_text에 맡긴다.
            # 단, 네가 원하면 status>=400일 때 type:error로 별도 처리도 가능.

            return raw_text


async def ws_chatbot(websocket: WebSocket):
    await websocket.accept()

    # 연결 안내
    await websocket.send_text(
        json.dumps(
            {
                "type": "message",
                "role": "assistant",
                "message": json.dumps(
                    {
                        "info": f"{BOT_NAME} 연결 완료",
                        "note": "봇 API 응답은 원문 JSON 문자열 그대로 전달됩니다.",
                    },
                    ensure_ascii=False,
                ),
            },
            ensure_ascii=False,
        )
    )

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"message": raw}

            user_text = _extract_user_message(payload)
            if not user_text:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "error",
                            "role": "system",
                            "message": "빈 메시지는 처리할 수 없습니다.",
                        },
                        ensure_ascii=False,
                    )
                )
                continue

            # typing
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "typing",
                        "role": "system",
                        "message": f"{BOT_NAME}이(가) 입력 중입니다…",
                    },
                    ensure_ascii=False,
                )
            )

            try:
                bot_raw = await call_bot_api_raw(user_text)
            except Exception as e:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "error",
                            "role": "system",
                            "message": f"봇 호출 실패: {str(e)}",
                        },
                        ensure_ascii=False,
                    )
                )
                continue

            # ✅ 여기서 파싱/가공 없이 그대로 전달
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "message",
                        "role": "assistant",
                        "message": bot_raw,
                    },
                    ensure_ascii=False,
                )
            )

    except WebSocketDisconnect:
        return
    except Exception as e:
        try:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "role": "system",
                        "message": f"서버 오류: {str(e)}",
                    },
                    ensure_ascii=False,
                )
            )
        except Exception:
            pass


routes = [
    Route("/", homepage),
    WebSocketRoute("/ws", ws_chatbot),
]

app = Starlette(routes=routes, middleware=middleware)
