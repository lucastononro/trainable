# trainable

AI-powered ML experimentation platform — local installer.

## Install

```bash
pip install trainable-ai
```

## Usage

```bash
mkdir trainable && cd trainable
trainable init
```

The wizard will:
1. Check that Docker is installed
2. Download the production Docker Compose file
3. Prompt for your API keys (Anthropic + Modal)
4. Write a `.env` file
5. Start the full stack

## Commands

| Command | Description |
|---------|-------------|
| `trainable init` | Setup wizard — downloads compose file, configures secrets, launches |
| `trainable up` | Start all services |
| `trainable down` | Stop all services |

## Requirements

- Docker with Compose plugin
- [Anthropic API key](https://console.anthropic.com/)
- [Modal account](https://modal.com/) — get tokens from [modal.com/settings](https://modal.com/settings)
