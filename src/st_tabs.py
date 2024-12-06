import json

import streamlit as st
import asyncio
import websockets
import asyncio
import configparser
import threading
from datetime import datetime
import os
import json
import shutil
import time

import requests
# import uptime
import streamlit as st
from streamlit_option_menu import option_menu
from streamlit.runtime.scriptrunner import add_script_run_ctx
from src.st_styles import *


def handle_workers(data: dict):
    st.write(data)


