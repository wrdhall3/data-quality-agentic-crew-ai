from crewai import Agent, Task, Crew
from crewai.tools import BaseTool
from typing import Dict, Any, List
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

class RuleExecutionTool(BaseTool):
    name: str = "Rule Execution Tool"
    description: str = "Executes data quality rules and returns violations"
    
    # Private attributes that won't be validated by Pydantic
    _engine: Any = PrivateAttr()

    def __init__(self, db_uri: str):
        super().__init__()
        self._engine = create_engine(db_uri)
    
    def _run(self, params: Dict[str, Any]) -> str:
        """
        Execute rules and return violations
        params should contain:
        - rule_ids: List of rule IDs to check, or None for all active rules
        - severity: Optional severity filter ('HIGH', 'MEDIUM', 'LOW')
        """
        try:
            with self._engine.connect() as conn:
                # Build the query to get rules
                rule_query = "SELECT * FROM Rules WHERE 1=1"
                query_params = {}
                
                if params.get('rule_ids'):
                    rule_query += " AND rule_id IN :rule_ids"
                    query_params['rule_ids'] = tuple(params['rule_ids'])
                
                if params.get('severity'):
                    rule_query += " AND severity = :severity"
                    query_params['severity'] = params['severity']
                
                # Get the rules
                rules = conn.execute(text(rule_query), query_params).fetchall()
                
                if not rules:
                    return "No rules found matching the criteria"
                
                # Execute each rule and collect violations
                all_violations = []
                for rule in rules:
                    try:
                        # Execute the rule query
                        result = pd.read_sql(rule.rule_text, conn)
                        
                        if not result.empty:
                            violation_count = len(result)
                            violation_summary = f"\n❌ Rule '{rule.rule_name}' (ID: {rule.rule_id}, Severity: {rule.severity})"
                            violation_summary += f"\nDescription: {rule.rule_description}"
                            violation_summary += f"\nFound {violation_count} violation(s):\n"
                            violation_summary += result.to_string()
                            all_violations.append(violation_summary)
                        else:
                            all_violations.append(f"\n✅ Rule '{rule.rule_name}' (ID: {rule.rule_id}) - No violations found")
                    
                    except Exception as e:
                        all_violations.append(f"\n⚠️ Error executing rule '{rule.rule_name}' (ID: {rule.rule_id}): {str(e)}")
                
                if not all_violations:
                    return "No violations found for any rules"
                
                return "\n".join(all_violations)
                
        except Exception as e:
            return f"Error checking rules: {str(e)}"

def main():
    # Database configuration
    DB_URI = "mysql+pymysql://root:Ridgewood2024@localhost:3306/trading_data"

    # Initialize tool
    rule_executor = RuleExecutionTool(DB_URI)

    # Create Agent
    rule_checker_agent = Agent(
        role='Rule Violation Checker',
        goal='Check trading data for rule violations',
        backstory="""You are a data quality analyst who checks trading data for rule violations.
        You understand SQL and can interpret violation results effectively.""",
        tools=[rule_executor],
        verbose=True,
        llm=llm
    )

    while True:
        print("\nCheck for rule violations:")
        print("1. Check all rules")
        print("2. Check specific rules")
        print("3. Check by severity")
        print("4. Exit")
        
        choice = input("\nEnter your choice (1-4): ")
        
        if choice == "4":
            print("Goodbye!")
            break
            
        params = {}
        
        if choice == "2":
            # Get specific rule IDs
            rule_ids_input = input("\nEnter rule IDs (comma-separated): ")
            try:
                rule_ids = [int(id.strip()) for id in rule_ids_input.split(",")]
                params['rule_ids'] = rule_ids
            except ValueError:
                print("Invalid rule IDs. Please enter numbers separated by commas.")
                continue
                
        elif choice == "3":
            # Get severity level
            print("\nSelect severity level:")
            print("1. HIGH")
            print("2. MEDIUM")
            print("3. LOW")
            sev_choice = input("Enter choice (1-3): ")
            
            severity_map = {"1": "HIGH", "2": "MEDIUM", "3": "LOW"}
            if sev_choice in severity_map:
                params['severity'] = severity_map[sev_choice]
            else:
                print("Invalid severity choice")
                continue

        # Create Task
        check_task = Task(
            description="Check trading data for rule violations",
            expected_output="A report of any records that violate the rules",
            agent=rule_checker_agent,
            tools=[rule_executor]  # Add tools explicitly
        )

        # Execute the task with parameters
        result = rule_checker_agent.execute_task(check_task, {"params": params})
        print("\nViolation Check Results:")
        print(result)

if __name__ == "__main__":
    main() 