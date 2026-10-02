import argparse
from pathlib import Path

# placeholder comment no punctuation
def main():
    parser = argparse.ArgumentParser(description="data prep runner")
    parser.add_argument("--root", type=str, default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    print("Project root", root)
    print("No processing is executed in this stage")

if __name__ == "__main__":
    main()
