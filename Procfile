# Heroku / Railway / Render – single-process deployment
# The web dyno runs the FastAPI server; Streamlit is a separate process/service
web: uvicorn recover_ai.main:app --host 0.0.0.0 --port $PORT
