from crewai import Agent, Task, Crew
from crewai.tools import BaseTool
from typing import Dict, Any
import sqlalchemy
from sqlalchemy import create_engine, text
import pandas as pd
from pydantic import Field, PrivateAttr
from langchain_openai import ChatOpenAI
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure OpenAI
llm = ChatOpenAI(
    model="gpt-4",
    temperature=0.7,
    api_key=os.getenv('OPENAI_API_KEY')
)

class NL2SQLTool(BaseTool):
    name: str = "Natural Language to SQL Converter"
    description: str = "Converts natural language queries to SQL based on the trading database schema"
    
    # Private attributes that won't be validated by Pydantic
    _db_uri: str = PrivateAttr()
    _engine: Any = PrivateAttr()
    _table_info: Dict = PrivateAttr(default={})

    def __init__(self, db_uri: str):
        super().__init__()
        self._db_uri = db_uri
        self._engine = create_engine(db_uri)
        
        # Get database metadata for context
        with self._engine.connect() as conn:
            # Get all table names
            tables = conn.execute(text("SHOW TABLES")).fetchall()
            
            # Get schema for each table
            for table in tables:
                table_name = table[0]
                columns = conn.execute(text(f"DESCRIBE {table_name}")).fetchall()
                self._table_info[table_name] = [col[0] for col in columns]

    def _run(self, query: str) -> Dict[str, str]:
        """
        Convert natural language to SQL based on the database schema
        """
        # Here you would typically use an LLM to convert natural language to SQL
        # For this example, we'll use a simple mapping
        # In a real implementation, you would want to use a more sophisticated approach
        
        # Example mapping for demonstration
        if "average quantity" in query.lower():
            return {"sql_query": "SELECT AVG(quantity) as avg_quantity FROM trades"}
        elif "total trades" in query.lower():
            return {"sql_query": "SELECT COUNT(*) as total_trades FROM trades"}
        else:
            return {"sql_query": "SELECT * FROM trades LIMIT 5"}

class SQLExecutionTool(BaseTool):
    name: str = "SQL Executor"
    description: str = "Executes SQL queries against the trading database"
    
    # Private attributes that won't be validated by Pydantic
    _engine: Any = PrivateAttr()

    def __init__(self, db_uri: str):
        super().__init__()
        self._engine = create_engine(db_uri)
    
    def _run(self, sql_query: str) -> str:
        """
        Execute SQL query and return results
        """
        try:
            with self._engine.connect() as conn:
                result = pd.read_sql(sql_query, conn)
                return result.to_string()
        except Exception as e:
            return f"Error executing query: {str(e)}"

# Database configuration
DB_URI = "mysql+pymysql://root:Ridgewood2024@localhost:3306/trading_data"

# Initialize tools
nl2sql_tool = NL2SQLTool(DB_URI)
sql_execution_tool = SQLExecutionTool(DB_URI)

# Create Agents
translator_agent = Agent(
    role='SQL Translator',
    goal='Convert natural language questions into SQL queries',
    backstory="""You are an expert in converting natural language questions into SQL queries.
    You understand database schemas and can create efficient SQL queries.""",
    tools=[nl2sql_tool],
    verbose=True,
    llm=llm
)

executor_agent = Agent(
    role='SQL Executor',
    goal='Execute SQL queries and return results',
    backstory="""You are a database expert who executes SQL queries and returns results in a clear format.
    You understand SQL and can interpret query results effectively.""",
    tools=[sql_execution_tool],
    verbose=True,
    llm=llm
)

# Create Tasks
translation_task = Task(
    description="Convert this question to SQL: What is the average quantity of shares being traded?",
    expected_output="A SQL query that calculates the average quantity from the trades table",
    agent=translator_agent
)

execution_task = Task(
    description="Execute the SQL query provided by the translator agent",
    expected_output="The results of the SQL query execution",
    agent=executor_agent
)

# Create and run the crew
crew = Crew(
    agents=[translator_agent, executor_agent],
    tasks=[translation_task, execution_task],
    verbose=True
)

# Run the crew
result = crew.kickoff()

print("Final Result:", result) 