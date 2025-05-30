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
import json

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
    _llm: Any = PrivateAttr()

    def __init__(self, db_uri: str):
        super().__init__()
        self._db_uri = db_uri
        self._engine = create_engine(db_uri)
        self._llm = ChatOpenAI(
            model="gpt-4",
            temperature=0,  # Use temperature 0 for more deterministic SQL generation
            api_key=os.getenv('OPENAI_API_KEY')
        )
        
        # Get database metadata for context
        with self._engine.connect() as conn:
            # Get all table names
            tables = conn.execute(text("SHOW TABLES")).fetchall()
            
            # Get schema for each table
            for table in tables:
                table_name = table[0]
                columns = conn.execute(text(f"DESCRIBE {table_name}")).fetchall()
                self._table_info[table_name] = [
                    {
                        "column": col[0],
                        "type": col[1],
                        "null": col[2],
                        "key": col[3],
                        "default": col[4],
                        "extra": col[5]
                    } for col in columns
                ]

    def _run(self, query: str) -> Dict[str, str]:
        """
        Convert natural language to SQL using GPT-4
        """
        # Prepare the database schema information for the prompt
        schema_info = json.dumps(self._table_info, indent=2)
        
        prompt = f"""You are an expert SQL query generator. Your task is to convert natural language questions into SQL queries.
        
        The database schema is structured as follows:
        {schema_info}

        Please convert the following question into a SQL query:
        "{query}"

        Rules:
        1. Return ONLY a dictionary with a single key 'sql_query' containing the SQL query string
        2. The query should be valid MySQL syntax
        3. Use proper SQL formatting with appropriate spacing and line breaks
        4. Include any necessary table joins if required
        5. Add appropriate aliases for readability
        6. Add comments to explain complex parts of the query
        7. Format numbers and dates according to MySQL standards
        8. Handle NULL values appropriately
        9. Use appropriate aggregate functions when needed
        10. Return the query in a format that can be directly executed

        Example response format:
        {{"sql_query": "SELECT column FROM table WHERE condition"}}
        """

        # Get response from LLM
        response = self._llm.invoke(prompt)
        
        try:
            # Try to extract just the SQL query if the response contains additional text
            if "```" in response.content:
                # Extract code from markdown code blocks
                sql_parts = response.content.split("```")
                for part in sql_parts:
                    if "SELECT" in part.upper() or "WITH" in part.upper():
                        return {"sql_query": part.strip().strip("sql").strip()}
            
            # Try to parse as JSON if it's in the correct format
            if "{" in response.content and "}" in response.content:
                try:
                    return json.loads(response.content)
                except:
                    pass
            
            # If no other format works, just extract any SQL-like string
            sql_query = response.content
            if isinstance(sql_query, str):
                sql_query = sql_query.strip()
                if sql_query.startswith('"') and sql_query.endswith('"'):
                    sql_query = sql_query[1:-1]
                return {"sql_query": sql_query}
            
            return {"sql_query": response.content}
            
        except Exception as e:
            return {"sql_query": f"Error generating SQL: {str(e)}"}

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