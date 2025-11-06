# Conversational Protein Search

A conversational AI assistant for searching and exploring proteins from UniProt using LangGraph workflows.

## Features

- 🧬 **Natural Language Protein Search**: Query proteins using conversational language
- 🔍 **Intelligent Entity Extraction**: Automatically extracts protein names, organisms, and refinement terms
- 🎯 **Smart Refinement**: Suggests refinements when results are too broad
- 🔄 **Human-in-the-Loop**: Interactive clarification when information is missing
- 📊 **Evaluation Dashboard**: View conversation evaluations and workflow traces
- ☁️ **Cloud Deployed**: Ready-to-use web UI on Google Cloud Run

## Architecture

- **LangGraph**: Stateful workflow orchestration with human-in-the-loop support
- **FastAPI**: REST API backend serving both API endpoints and UI
- **UniProt MCP Server**: Node.js microservice for UniProt API access
- **Predicta.bio LLM**: Custom LLM for protein domain understanding
- **LangSmith**: Observability and evaluation framework

## Quick Start

### Local Development

1. **Install Dependencies**

```bash
cd client
pip install -r requirements.txt
```

2. **Set Environment Variables**

Create `client/.env`:
```
# LangSmith Configuration
LANGCHAIN_API_KEY={value}
LANGCHAIN_PROJECT={value}
LANGCHAIN_TRACING_V2=true
LANGCHAIN_ENDPOINT={value}
GEMINI_API_KEY={value}
# MCP Server Configuration
MCP_SERVER_PATH={path to /mcp-server/build/index.js}                                                       

# LLM API Configuration
BASE_URL={value}
MODEL_NAME={value}
API_KEY={value}
```

3. **Run Locally**

```bash
# Start API server
python client/api_service.py

# In another terminal, start UI server
python serve_chat.py
```

Access UI at: http://localhost:8081/chat_ui.html

### Docker

```bash
# Build
docker build -t uniprot-app .

# Run
docker run -p 8001:8001 -p 8081:8081 \
  -e BASE_URL={value} \
  -e MODEL_NAME={value} \
  -e API_KEY={value} \
  uniprot-app
```

### Cloud Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed deployment instructions.

## Project Structure

```
.
├── client/                      # Python application
│   ├── api_service.py          # FastAPI server (API + UI)
│   ├── conversational_protein_client.py  # LangGraph workflow
│   ├── protein_search_client.py # UniProt API client
│   └── requirements.txt
├── mcp-server/                 # UniProt MCP server (Node.js)
│   └── build/
├── chat_ui.html                # Main chat interface
├── evalsview.html              # Evaluation dashboard
├── Dockerfile                  # Container definition
└── DEPLOYMENT.md              # Deployment guide
```

## API Endpoints

- `GET /` - Main chat UI
- `GET /health` - Health check
- `POST /query` - Process protein search query
- `POST /evals` - Run LLM-as-Judge evaluation
- `GET /evalsview` - Evaluation dashboard
- `GET /sessions` - List evaluation sessions

## Workflow

1. **Entity Extraction**: Extracts protein name, organism, and refinement terms
2. **Search**: Queries UniProt with extracted parameters
3. **Refinement**: If >10 results, suggests refinements
4. **Retry**: If 0 results, generates alternate protein names
5. **Details**: If 1-10 results, shows protein details
6. **Selection**: User selects specific protein for details

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `BASE_URL` | LLM API endpoint | Yes |
| `MODEL_NAME` | Model name/path | Yes |
| `API_KEY` | LLM API key | Yes |
| `LANGCHAIN_API_KEY` | LangSmith API key | No |
| `GEMINI_API_KEY` | Gemini API key (for evaluation) | No |
| `MCP_SERVER_PATH` | Path to MCP server | No (default: `/app/mcp-server/build/index.js`) |

## Documentation

- [OVERVIEW.md](OVERVIEW.md) - Technical overview and workflow explanation
- [DEPLOYMENT.md](DEPLOYMENT.md) - Deployment instructions
- [EVALUATION_SUMMARY.md](EVALUATION_SUMMARY.md) - Evaluation framework details

## License

MIT

