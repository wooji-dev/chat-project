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

# 필요 시: 허용 오리진 추가
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


def _safe_extract_user_message(payload: dict) -> str:
    """
    클라이언트 프로토콜:
      { "message": "<사용자 입력>" }
    예외적으로 text/userMessage 등의 키가 들어와도 최대한 흡수.
    """
    for key in ("message", "text", "userMessage", "m"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


async def call_bot_api(user_text: str) -> str:
    """
    기존 코드와 최대한 호환:
      GET BOT_API_URL?m=<user_text>
    응답 포맷:
      r['choices'][0]['message']['content'] 를 우선 사용
    """
    params = {"m": user_text}

    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(BOT_API_URL, params=params) as resp:
            # 에러 핸들
            if resp.status >= 400:
                body = await resp.text()
                raise RuntimeError(f"BOT_API HTTP {resp.status}: {body[:300]}")
            r = await resp.json()

    # 가능한 응답 포맷들을 유연하게 처리
    try:
        return (
            r.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        ) or ""
    except Exception:
        # 최후: 전체를 문자열로
        return json.dumps(r, ensure_ascii=False)


async def ws_chatbot(websocket: WebSocket):
    await websocket.accept()

    # 연결 직후 안내(선택)
    await websocket.send_text(
        json.dumps(
            {
                "type": "message",
                "role": "assistant",
                "message": f"{BOT_NAME}입니다. 무엇을 도와드릴까요?",
            },
            ensure_ascii=False,
        )
    )

    try:
        while True:
            raw = await websocket.receive_text()

            payload = None
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"message": raw}

            user_text = _safe_extract_user_message(payload)
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

            # typing 알림
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

            # 봇 호출
            try:
                bot_text = await call_bot_api(user_text)
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

            bot_text = (bot_text or "").strip()
            if not bot_text:
                bot_text = "응답을 생성하지 못했습니다. 다시 시도해 주세요."

            await websocket.send_text(
                json.dumps(
                    {
                        "type": "message",
                        "role": "assistant",
                        "message": bot_text,
                    },
                    ensure_ascii=False,
                )
            )

    except WebSocketDisconnect:
        # 정상 종료
        return
    except Exception as e:
        # 예기치 못한 오류
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
