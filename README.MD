# AI Stock Intelligence Platform

## Overview
This project is an end-to-end stock analytics system that ingests market data, computes technical indicators, generates AI-powered insights, and visualizes results through dashboards and automated workflows.

The system integrates a Python data pipeline, a PostgreSQL database, a FastAPI backend, an LLM-based summary service, n8n automation, and Power BI and Tableau dashboards.

---

## Features
- Automated stock data ingestion using Python and yfinance  
- PostgreSQL database for structured time-series storage  
- FastAPI backend for serving analytics and data endpoints  
- AI-generated market summaries using a large language model (Groq)  
- Scheduled automation using n8n workflows  
- Interactive dashboards built in Power BI and Tableau  

---

## Architecture

```
Data Pipeline (Python)
        ↓
PostgreSQL Database
        ↓
FastAPI Backend
        ↓
AI Summary Service (LLM via Groq)
        ↓
n8n Automation (Scheduled Workflow)
        ↓
Email / Alerts
        ↓
Power BI & Tableau Dashboards
```

---

## Project Structure

```
ai-stock-intelligence-platform/
│
├── backend/
│   ├── main.py
│   ├── db.py
│   ├── models.py
│   ├── ai_summary.py
│   └── init_db.py
│
├── scripts/
│   └── fetch_data.py
│
├── data/
│   ├── raw/
│   └── processed/
│
├── dashboards/
│   ├── powerbi.png
│   └── tableau.png
│
├── automation/
│   ├── n8n_workflow.json
│   └── n8n_workflow.png
│
├── .env
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Getting Started

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set environment variables
Create a `.env` file:
```env
DATABASE_URL=postgresql://stock_user:stock_pass@localhost:5432/stock_db
GROQ_API_KEY=your_api_key_here
```

### 3. Initialize the database
```bash
python -m backend.init_db
```

### 4. Run data pipeline
```bash
python scripts/fetch_data.py
```

### 5. Start backend server
```bash
uvicorn backend.main:app --reload
```

### 6. Access API
```
http://127.0.0.1:8000/docs
```

### 7. Run automation
Start n8n and execute the workflow.

---

## API Endpoints

- `/stocks` → Returns stock data (optional ticker filter)  
- `/latest` → Latest stock snapshot  
- `/signals` → All detected signals  
- `/signals/latest` → Latest signals only  
- `/ai-summary/latest` → AI-generated market summary  

---

## Automation

The platform includes an n8n workflow that:
- Runs on a scheduled trigger  
- Calls the `/ai-summary/latest` endpoint  
- Generates AI-based market summaries  
- Sends notifications via email or webhook  

---

## Dashboards

### Power BI
Operational dashboard for:
- Stock trends  
- Signals  
- Key metrics  

![Power BI](dashboards/powerbi.png)

### Tableau
Executive dashboard for:
- Market storytelling  
- Top movers  
- Signal insights  

![Tableau](dashboards/tableau.png)
