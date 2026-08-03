from setuptools import setup, find_packages

setup(
    name="codetest",
    version="0.1.0",
    # 파이썬 패키지가 'local-client' 폴더 안에 있음을 명시
    package_dir={"": "local-client"},
    packages=find_packages(where="local-client"),
    
    # 💡 사용된 모든 외부 라이브러리를 여기에 나열합니다.
    install_requires=[
        "typer",      # CLI 명령어 인터페이스 구성
        "rich",       # 터미널 UI(TUI) 디자인 및 출력
        "requests",   # API 서버 통신 클라이언트 (만약 httpx를 쓰셨다면 httpx로 변경)
        # 참고: 만약 pydantic 같은 데이터 검증 라이브러리를 추가로 쓰셨다면 여기에 이어서 적어주세요!
    ],
    
    entry_points={
        "console_scripts": [
            # 터미널에서 'codetest' 입력 시 실행될 메인 함수 연결
            "codetest=codetest.__main__:main", 
        ],
    },
)