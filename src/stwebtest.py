import streamlit as st
import asyncio
import websockets

st.write('begin')

async def listen_to_server():
    uri = "ws://localhost:8000/ws"  # WebSocket-сервер
    async with websockets.connect(uri) as websocket:
        while True:
            message = await websocket.recv()
            st.button(message)
            st.rerun()

# Запуск корутины с WebSocket в Streamlit
asyncio.run(listen_to_server())
