from setuptools import setup, find_packages

setup(
    name="codetest",
    version="0.1.0",
    # 1. 파이썬에게 실제 패키지들이 'local-client' 폴더 안에 있다고 알려줍니다.
    package_dir={"": "local-client"},
    packages=find_packages(where="local-client"),
    
    install_requires=[
        # 필요한 외부 라이브러리가 있다면 여기에 추가
    ],
    entry_points={
        "console_scripts": [
            # 2. 터미널에서 codetest를 치면 -> codetest 폴더의 __main__.py 안의 main() 함수를 실행해라
            "codetest=codetest.__main__:main", 
        ],
    },
)