# 🤝 Contributing to DEDAN Remote

Contributions are welcome! This project grew out of the AIJobFinder system — see the
[AIJobFinder contributing guide](CONTRIBUTING.md) for the original workflow, adapted
here for the DEDAN Remote public release.

## Development Setup

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/yourusername/dedan-remote.git
   cd dedan-remote
   ```
3. **Create a feature branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Set up the environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   # Fill in real credentials for notification channels you'll test
   ```
5. **Initialize the database**:
   ```bash
   python main.py --setup
   ```
6. **Run the test suite**:
   ```bash
   pytest tests/ -v
   ```
7. **Check code style**:
   ```bash
   black . --check
   isort --check-only .
   flake8 . --max-line-length=100 --extend-ignore=E203,W503 \
     --exclude=.venv,venv,__pycache__,build,dist
   ```
8. **Commit your changes** with a clear message
9. **Push to your fork** and open a Pull Request

## Pull Request Guidelines

- **One feature or bugfix per PR**
- **Add tests** for new functionality
- **Update documentation** if user-facing behavior changes
- **Keep the build passing** — CI runs tests, linting, and type checks on every PR
- **Use the existing commit message convention** (see below)

## Commit Message Convention

```
feat: add new scraper for <platform>
fix: correct scoring weight for <dimension>
docs: update README with <topic>
refactor: simplify ranking agent logic
test: add property-based test for <component>
```

## Reporting Issues

- Use the [GitHub Issues](https://github.com/yourusername/dedan-remote/issues) tracker
- Include: platform/feature name, steps to reproduce, expected vs actual behavior
- Label issues appropriately: `question`, `bug`, `enhancement`, `good first issue`

## License

By contributing, you agree that your contributions are released under the [MIT License](LICENSE).