import streamlit as st

# Инициализация session_state
if 'counter' not in st.session_state:
    st.session_state.counter = 0
if 'name' not in st.session_state:
    st.session_state.name = "Guest"


# Функция для увеличения счетчика
def increment_counter():
    st.session_state.counter += 1


# Функция для обновления имени
def update_name():
    st.session_state.name = st.text_input("Enter your name", key='name_input')


st.title("Multi-Variable Session State Example")

# Отображение текущего значения счетчика
st.write(f"Counter value: {st.session_state.counter}")

# Кнопка для увеличения счетчика
st.button("Increment", on_click=increment_counter)

# Кнопка для обновления имени
if st.button("Submit"):
    update_name()
# Отображение имени
st.write(f"Hello, {st.session_state.name}")
