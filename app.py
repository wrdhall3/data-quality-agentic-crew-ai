import streamlit as st
from crewai import Agent, Task, Crew
from crewai.tools import BaseTool
from typing import Dict, Any
import sqlalchemy
from sqlalchemy import create_engine, text
import pandas as pd
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

# Database configuration
DB_URI = "mysql+pymysql://root:Ridgewood2024@localhost:3306/trading_data"

# Import your tools
from create_rule import RuleGeneratorTool
from check_violations import RuleExecutionTool
from generic_query import NL2SQLTool, SQLExecutionTool

def init_session_state():
    if 'rule_generator' not in st.session_state:
        st.session_state.rule_generator = RuleGeneratorTool(DB_URI)
    if 'rule_executor' not in st.session_state:
        st.session_state.rule_executor = RuleExecutionTool(DB_URI)
    if 'nl2sql_tool' not in st.session_state:
        st.session_state.nl2sql_tool = NL2SQLTool(DB_URI)
    if 'sql_execution_tool' not in st.session_state:
        st.session_state.sql_execution_tool = SQLExecutionTool(DB_URI)

def create_agents():
    rule_creator_agent = Agent(
        role='Data Quality Rule Creator',
        goal='Create SQL-based data quality rules for trading data',
        backstory="""You are an expert in data quality and trading data. You create rules to ensure 
        the integrity and quality of trading data. You understand both business requirements and SQL.""",
        tools=[st.session_state.rule_generator],
        verbose=True,
        llm=llm
    )

    rule_checker_agent = Agent(
        role='Rule Violation Checker',
        goal='Check trading data for rule violations',
        backstory="""You are a data quality analyst who checks trading data for rule violations.
        You understand SQL and can interpret violation results effectively.""",
        tools=[st.session_state.rule_executor],
        verbose=True,
        llm=llm
    )

    translator_agent = Agent(
        role='SQL Translator',
        goal='Convert natural language questions into SQL queries',
        backstory="""You are an expert in converting natural language questions into SQL queries.
        You understand database schemas and can create efficient SQL queries.""",
        tools=[st.session_state.nl2sql_tool],
        verbose=True,
        llm=llm
    )

    executor_agent = Agent(
        role='SQL Executor',
        goal='Execute SQL queries and return results',
        backstory="""You are a database expert who executes SQL queries and returns results in a clear format.
        You understand SQL and can interpret query results effectively.""",
        tools=[st.session_state.sql_execution_tool],
        verbose=True,
        llm=llm
    )

    return rule_creator_agent, rule_checker_agent, translator_agent, executor_agent

def create_rule(agent, rule_description):
    creation_task = Task(
        description=f"Create a data quality rule for this requirement: {rule_description}",
        expected_output="A complete rule definition including name, description, severity, and SQL query",
        agent=agent
    )
    return agent.execute_task(creation_task)

def check_violations(agent, params=None):
    check_task = Task(
        description="Check trading data for rule violations",
        expected_output="A report of any records that violate the rules",
        agent=agent,
        tools=[st.session_state.rule_executor]
    )
    return agent.execute_task(check_task, {"params": params} if params else {})

def query_database(translator_agent, executor_agent, question):
    translation_task = Task(
        description=f"Convert this question to SQL: {question}",
        expected_output="A SQL query that answers the user's question",
        agent=translator_agent
    )

    # Get the SQL query
    sql_result = translator_agent.execute_task(translation_task)
    
    # Extract the SQL query from the result
    if isinstance(sql_result, dict) and 'sql_query' in sql_result:
        sql_query = sql_result['sql_query']
    elif isinstance(sql_result, str):
        # Try to extract SQL query from the string response
        if "sql_query" in sql_result.lower():
            try:
                sql_query = json.loads(sql_result)['sql_query']
            except:
                sql_query = sql_result
        else:
            sql_query = sql_result

    # Execute the query using the agent first (for debug output)
    execution_task = Task(
        description="Execute the SQL query and return results",
        expected_output="The results of the SQL query execution",
        agent=executor_agent
    )
    
    # Let the agent execute for debug output
    agent_result = executor_agent.execute_task(execution_task, {"sql_query": sql_query})
    
    # Now get the actual DataFrame for display
    try:
        with create_engine(DB_URI).connect() as conn:
            df_result = pd.read_sql(sql_query, conn)
            
            # If we have actual rows of data
            if not df_result.empty and len(df_result.columns) > 1 or len(df_result) > 1:
                return {
                    'sql_query': sql_query,
                    'results': df_result,
                    'is_table': True
                }
            # For single value results or empty results
            else:
                return {
                    'sql_query': sql_query,
                    'results': agent_result,
                    'is_table': False
                }
    except Exception as e:
        return {
            'sql_query': sql_query,
            'results': f"Error executing query: {str(e)}",
            'is_table': False
        }

def main():
    st.set_page_config(page_title="Data Quality Monitor", layout="wide")
    
    st.title("Data Quality Monitoring System")
    
    # Initialize session state
    init_session_state()
    
    # Create agents
    rule_creator_agent, rule_checker_agent, translator_agent, executor_agent = create_agents()
    
    # Sidebar for navigation
    page = st.sidebar.radio("Select Operation", ["Create Rule", "Check Violations", "Query Database"])
    
    if page == "Create Rule":
        st.header("Create Data Quality Rule")
        
        # Rule creation form
        with st.form("rule_creation_form"):
            rule_description = st.text_area(
                "Rule Description",
                placeholder="Example: Ensure all trade quantities are positive numbers"
            )
            submit_button = st.form_submit_button("Create Rule")
            
            if submit_button and rule_description:
                with st.spinner("Creating rule..."):
                    try:
                        result = create_rule(rule_creator_agent, rule_description)
                        st.success("Rule created successfully!")
                        st.json(result)
                    except Exception as e:
                        st.error(f"Error creating rule: {str(e)}")
    
    elif page == "Check Violations":
        st.header("Check Rule Violations")
        
        # Get existing rules from database
        engine = create_engine(DB_URI)
        with engine.connect() as conn:
            rules = pd.read_sql("SELECT rule_id, rule_name, severity FROM Rules", conn)
        
        # Violation checking options
        check_type = st.radio(
            "Select Check Type",
            ["All Rules", "Specific Rules", "By Severity"]
        )
        
        params = {}
        
        if check_type == "Specific Rules":
            selected_rules = st.multiselect(
                "Select Rules to Check",
                options=rules['rule_id'].tolist(),
                format_func=lambda x: f"Rule {x}: {rules[rules['rule_id']==x]['rule_name'].iloc[0]}"
            )
            if selected_rules:
                params['rule_ids'] = selected_rules
        
        elif check_type == "By Severity":
            severity = st.selectbox(
                "Select Severity Level",
                ["HIGH", "MEDIUM", "LOW"]
            )
            if severity:
                params['severity'] = severity
        
        if st.button("Check Violations"):
            with st.spinner("Checking violations..."):
                try:
                    result = check_violations(rule_checker_agent, params)
                    st.subheader("Violation Check Results")
                    st.text(result)
                except Exception as e:
                    st.error(f"Error checking violations: {str(e)}")
    
    else:  # Query Database
        st.header("Query Trading Database")
        
        # Query input form
        with st.form("query_form"):
            question = st.text_area(
                "Enter your question about the trading data",
                placeholder="Example: What is the average trade quantity by trader in the last month?"
            )
            submit_button = st.form_submit_button("Run Query")
            
            if submit_button and question:
                with st.spinner("Processing query..."):
                    try:
                        result = query_database(translator_agent, executor_agent, question)
                        
                        # Display the SQL query
                        st.subheader("Generated SQL Query")
                        st.code(result['sql_query'], language='sql')
                        
                        # Display the results
                        st.subheader("Query Results")
                        if result['is_table']:
                            st.dataframe(
                                result['results'],
                                use_container_width=True,
                                hide_index=True
                            )
                        else:
                            st.text(result['results'])
                        
                    except Exception as e:
                        st.error(f"Error processing query: {str(e)}")

if __name__ == "__main__":
    main() 