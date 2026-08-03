from setuptools import setup, find_packages

setup(
    name="codetest",
    version="0.1.0",
    # 파이썬 패키지가 'local-client' 폴더 안에 있음을 명시
    package_dir={"": "local-client"},
    packages=find_packages(where="local-client"),
    
    python_requires=">=3.10",

    # local-client 가 실제로 import 하는 외부 패키지 (그 외는 전부 표준 라이브러리)
    install_requires=[
        "typer>=0.12",   # CLI 명령어 인터페이스 (cli.py)
        "rich>=13.7",    # TUI 표/패널 출력 (tui/renderer.py)
        "httpx>=0.27",   # Agent Server REST 호출 (api_client.py)
    ],

    entry_points={
        "console_scripts": [
            # Typer 앱 객체를 그대로 연결한다 (__main__ 에는 main 함수가 없음)
            "codetest=codetest.cli:app",
        ],
    },
)