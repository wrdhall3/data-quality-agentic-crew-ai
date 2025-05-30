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

class RuleGeneratorTool(BaseTool):
    name: str = "Data Quality Rule Generator"
    description: str = "Converts natural language rule descriptions into SQL-based data quality rules"
    
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
            # Get Trades table schema
            columns = conn.execute(text("DESCRIBE trades")).fetchall()
            self._table_info["trades"] = [
                {
                    "column": col[0],
                    "type": col[1],
                    "null": col[2],
                    "key": col[3],
                    "default": col[4],
                    "extra": col[5]
                } for col in columns
            ]

    def _run(self, rule_description: str) -> Dict[str, Any]:
        """
        Convert natural language rule description into a data quality rule and save it to the database
        """
        # Prepare the database schema information for the prompt
        schema_info = json.dumps(self._table_info, indent=2)
        
        prompt = f"""You are an expert in creating data quality rules for trading data. Your task is to convert a natural language rule description into a SQL query that will identify violations of that rule.

        The Trades table schema is as follows:
        {schema_info}

        Please create a data quality rule based on this description:
        "{rule_description}"

        Requirements:
        1. The SQL query should identify records that VIOLATE the rule (not the ones that follow it)
        2. The query must be valid MySQL syntax
        3. The query should only reference the trades table
        4. Use proper SQL formatting with appropriate spacing and line breaks
        5. Add comments to explain the logic
        6. Handle NULL values appropriately
        7. The query will be stored in the rule_text column of the Rules table

        Return a JSON object with the following structure:
        {{
            "rule_name": "A short, descriptive name for the rule",
            "rule_description": "A clear description of what the rule checks",
            "severity": "Either 'HIGH', 'MEDIUM', or 'LOW' based on the importance of the rule",
            "rule_text": "The SQL query that identifies violations"
        }}

        Example response for "Ensure all trade quantities are positive":
        {{
            "rule_name": "Positive Trade Quantity",
            "rule_description": "All trade quantities must be greater than zero",
            "severity": "HIGH",
            "rule_text": "SELECT * FROM trades WHERE quantity <= 0 OR quantity IS NULL"
        }}
        """

        # Get response from LLM
        response = self._llm.invoke(prompt)
        
        try:
            # Try to parse the response as JSON
            if "{" in response.content and "}" in response.content:
                # Find the first { and last } to handle any extra text
                start = response.content.find("{")
                end = response.content.rfind("}") + 1
                json_str = response.content[start:end]
                rule_data = json.loads(json_str)
                
                # Validate the required fields
                required_fields = ["rule_name", "rule_description", "severity", "rule_text"]
                if not all(field in rule_data for field in required_fields):
                    raise ValueError("Missing required fields in rule data")
                
                # Validate severity
                if rule_data["severity"] not in ["HIGH", "MEDIUM", "LOW"]:
                    rule_data["severity"] = "MEDIUM"
                
                # Extract only the SQL query from rule_text by removing comments
                sql_text = rule_data["rule_text"]
                if "/*" in sql_text and "*/" in sql_text:
                    # Remove multi-line comments
                    start = sql_text.find("*/") + 2
                    sql_text = sql_text[start:].strip()
                
                # Insert the rule into the database
                with self._engine.connect() as conn:
                    insert_query = text("""
                        INSERT INTO Rules (rule_name, rule_description, rule_text, severity)
                        VALUES (:name, :description, :text, :severity)
                    """)
                    
                    result = conn.execute(insert_query, {
                        "name": rule_data["rule_name"],
                        "description": rule_data["rule_description"],
                        "text": sql_text,
                        "severity": rule_data["severity"]
                    })
                    conn.commit()
                    
                    # Add the rule_id to the response
                    rule_data["rule_id"] = result.lastrowid
                    # Update rule_text to show the clean SQL in the response
                    rule_data["rule_text"] = sql_text
                
                return rule_data
            
            raise ValueError("Could not find JSON data in response")
            
        except Exception as e:
            return {
                "rule_name": "Error",
                "rule_description": str(e),
                "severity": "HIGH",
                "rule_text": "Error generating rule",
                "error": True
            }

def main():
    # Database configuration
    DB_URI = "mysql+pymysql://root:Ridgewood2024@localhost:3306/trading_data"

    # Initialize tool
    rule_generator = RuleGeneratorTool(DB_URI)

    # Create Agent
    rule_creator_agent = Agent(
        role='Data Quality Rule Creator',
        goal='Create SQL-based data quality rules for trading data',
        backstory="""You are an expert in data quality and trading data. You create rules to ensure 
        the integrity and quality of trading data. You understand both business requirements and SQL.""",
        tools=[rule_generator],
        verbose=True,
        llm=llm
    )

    while True:
        # Get user input
        print("\nDescribe a data quality rule for the Trades table (or 'quit' to exit):")
        print("Example: 'Ensure all trade quantities are positive numbers'")
        rule_description = input("> ")
        
        # Check if user wants to quit
        if rule_description.lower() in ['quit', 'exit', 'q']:
            print("Goodbye!")
            break

        # Create Task
        creation_task = Task(
            description=f"Create a data quality rule for this requirement: {rule_description}",
            expected_output="A complete rule definition including name, description, severity, and SQL query",
            agent=rule_creator_agent
        )

        # Create the rule
        rule_result = rule_creator_agent.execute_task(creation_task)
        
        try:
            # Parse the rule result
            if isinstance(rule_result, str):
                # Check if there's a dictionary in the response
                if "{" in rule_result and "}" in rule_result:
                    start = rule_result.find("{")
                    end = rule_result.rfind("}") + 1
                    json_str = rule_result[start:end]
                    
                    # Handle the case where we have a Python dict string
                    if "'" in json_str:
                        try:
                            # Try to evaluate it as a Python literal
                            import ast
                            rule_data = ast.literal_eval(json_str)
                        except:
                            # If that fails, try manual JSON conversion
                            json_str = json_str.replace("'", '"')
                            # But preserve escaped quotes in SQL
                            json_str = json_str.replace('"\\"', "'")
                            json_str = json_str.replace('\\"', "'")
                            rule_data = json.loads(json_str)
                    else:
                        rule_data = json.loads(json_str)
                else:
                    # Try to parse comma-separated format first
                    try:
                        rule_data = {}
                        # Remove any prefix text before the actual data
                        if ":" in rule_result:
                            # Split on the first colon if it's a prefix like "The complete rule definition is as follows:"
                            parts = rule_result.split(":", 1)
                            if not any(key in parts[0].lower() for key in ["name", "description", "severity", "sql", "id"]):
                                rule_result = parts[1].strip()
                        
                        # Split by comma and handle each field
                        if "," in rule_result and ":" in rule_result:
                            fields = [f.strip() for f in rule_result.split(",")]
                            for field in fields:
                                if ":" not in field:
                                    continue
                                key, value = [x.strip() for x in field.split(":", 1)]
                                # Clean up quotes
                                value = value.strip("'").strip('"')
                                
                                if key.lower() == "name":
                                    rule_data["rule_name"] = value
                                elif key.lower() == "description":
                                    rule_data["rule_description"] = value
                                elif key.lower() == "severity":
                                    rule_data["severity"] = value
                                elif key.lower() in ["sql query", "rule sql"]:
                                    rule_data["rule_text"] = value
                                elif key.lower() == "rule id":
                                    try:
                                        rule_data["rule_id"] = int(value)
                                    except ValueError:
                                        pass
                        else:
                            # Line by line parsing
                            lines = [line.strip().lstrip('-').strip() for line in rule_result.split("\n") if line.strip() and ":" in line]
                            for line in lines:
                                if "Name:" in line:
                                    rule_data["rule_name"] = line.split("Name:")[1].strip().strip("'\"")
                                elif "Description:" in line:
                                    rule_data["rule_description"] = line.split("Description:")[1].strip().strip("'\"")
                                elif "Severity:" in line:
                                    rule_data["severity"] = line.split("Severity:")[1].strip().strip("'\"")
                                elif any(query_variant in line for query_variant in ["SQL Query:", "SQL query:", "Rule SQL:"]):
                                    # Find which variant is in the line
                                    query_key = next(key for key in ["SQL Query:", "SQL query:", "Rule SQL:"] if key in line)
                                    rule_data["rule_text"] = line.split(query_key)[1].strip().strip("'\"")
                                elif "Rule ID:" in line:
                                    try:
                                        rule_data["rule_id"] = int(line.split("Rule ID:")[1].strip())
                                    except ValueError:
                                        pass
                    except Exception as e:
                        print(f"\nError parsing response: {str(e)}")
                        print("Raw response:", rule_result)
                        continue
                    
                    # Verify we have all required fields
                    required_fields = ["rule_name", "rule_description", "severity", "rule_text"]
                    missing_fields = [field for field in required_fields if field not in rule_data]
                    if missing_fields:
                        print("\nError: Missing required fields:", ", ".join(missing_fields))
                        print("Raw response:", rule_result)
                        print("Parsed data:", rule_data)
                        continue
            elif isinstance(rule_result, dict):
                rule_data = rule_result
            else:
                print("\nError: Unexpected response type from agent")
                print("Raw response:", rule_result)
                continue
                
            if "error" in rule_data:
                print("\nError creating rule:", rule_data.get("rule_description"))
                continue
                
            # Display the created rule
            print("\nRule Created Successfully!")
            print("\nRule Details:")
            print(f"ID: {rule_data.get('rule_id', 'N/A')}")
            print(f"Name: {rule_data.get('rule_name', 'N/A')}")
            print(f"Description: {rule_data.get('rule_description', 'N/A')}")
            print(f"Severity: {rule_data.get('severity', 'N/A')}")
            print(f"\nRule SQL:\n{rule_data.get('rule_text', 'N/A')}")
            
        except Exception as e:
            print(f"\nError processing results: {str(e)}")
            print("Raw response:", rule_result)

if __name__ == "__main__":
    main() 