import os
import subprocess
import sys


def build():
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name",
        "GitHubHostManager",
        "--add-data",
        ".env.example;.",
        "app.py",
    ]
    print("Executando build do executável...")
    subprocess.run(command, check=True)
    print("Build concluído. O executável foi gerado na pasta dist/")


if __name__ == "__main__":
    build()
