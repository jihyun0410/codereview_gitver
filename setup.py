from setuptools import setup, find_packages

setup(
    name="codetest",               # 설치될 패키지 이름
    version="0.1.0",
    packages=find_packages(),      # 소스코드 폴더 자동 탐색
    install_requires=[             # 파이썬 코드가 사용하는 외부 라이브러리 (필요한 경우 작성)
        # 예: "requests", "fastapi" 
    ],
    entry_points={
        "console_scripts": [
            # 핵심! 터미널에서 'codetest' 명령어를 쳤을 때 실행될 파이썬 함수를 연결합니다.
            # "명령어이름=폴더명.파일명:실행할함수명" 구조입니다. (실제 코드 구조에 맞게 수정 필요)
            "codetest=my_package.main:main", 
        ],
    },
)