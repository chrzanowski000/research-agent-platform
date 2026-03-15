import os
import re
import shutil
import subprocess
from typing import TypedDict
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from dotenv import load_dotenv

load_dotenv()

# Updated State
class State(TypedDict):
    user_input: str
    latex_code: str
    filename: str
    pdf_path: str
    error: str
    iteration: int

@tool
def compile_latex_to_pdf(latex_content: str, filename: str) -> str:
    """Saves code to .tex and compiles PDF, organizing files into separate folders."""
    # Define Desktop and project folders
    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    projects_folder = os.path.join(desktop_path, "latex_projects")
    
    pdf_dir = os.path.join(projects_folder, "pdf_files")
    tex_dir = os.path.join(projects_folder, "tex_files")
    log_dir = os.path.join(projects_folder, "log_files")
    aux_dir = os.path.join(projects_folder, "aux_files")
    
    # Create all directories if they don't exist
    for directory in [projects_folder, pdf_dir, tex_dir, log_dir, aux_dir]:
        os.makedirs(directory, exist_ok=True)
    
    # Handle duplicate filenames based on the PDF directory
    base_filename = filename
    counter = 1
    pdf_filepath = os.path.join(pdf_dir, f"{base_filename}.pdf")
    
    while os.path.exists(pdf_filepath):
        base_filename = f"{filename}({counter})"
        pdf_filepath = os.path.join(pdf_dir, f"{base_filename}.pdf")
        counter += 1
        
    final_filename = base_filename
    
    # Write the .tex file directly into the tex/ directory
    tex_filepath = os.path.join(tex_dir, f"{final_filename}.tex")
    with open(tex_filepath, "w", encoding="utf-8") as f:
        f.write(latex_content)
        
    # Compile
    try:
        # We tell pdflatex to put ALL generated auxiliary files and the PDF into pdf_dir
        # We run it from the tex directory so it finds the source file easily
        subprocess.run(
            [
                "pdflatex", 
                "-interaction=nonstopmode", 
                f"-output-directory={pdf_dir}",
                tex_filepath
            ],
            cwd=tex_dir,
            capture_output=True,
            text=True,
            check=True
        )
        
        # Move generated auxiliary files from pdf_dir to their respective folders
        aux_filepath = os.path.join(pdf_dir, f"{final_filename}.aux")
        log_filepath = os.path.join(pdf_dir, f"{final_filename}.log")
        
        if os.path.exists(aux_filepath):
            shutil.move(aux_filepath, os.path.join(aux_dir, f"{final_filename}.aux"))
        if os.path.exists(log_filepath):
            shutil.move(log_filepath, os.path.join(log_dir, f"{final_filename}.log"))
            
        return f"Success! PDF generated at {pdf_filepath}"
        
    except subprocess.CalledProcessError as e:
        error_log = e.output[-1000:] if e.output else "Unknown compilation error"
        return f"Compilation Error:\n{error_log}"
    except FileNotFoundError:
        return "Compilation Error: pdflatex command not found on the system."


def generate_latex_node(state: State) -> dict:
    llm = ChatOpenAI(
        model=os.getenv("MODEL_NAME", "nvidia/nemotron-3-nano-30b-a3b:free"),
        temperature=0.2,
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.getenv("OPENROUTER_API_KEY")
    )
    
    sys_prompt = SystemMessage(
        content="You are an expert LaTeX developer and academic editor. Your task is to process the user's text into a valid, compilable LaTeX document. "
                "Instructions:\n"
                "1. Invent a short, snake_case filename based on the topic of the text. Put it on the VERY FIRST line as a comment like this: % filename: your_topic_name\n"
                "2. Output ONLY the raw LaTeX code below the comment. Do NOT wrap it in markdown formatting (like ```latex).\n"
                "3. Include proper structure: \\documentclass{article}, \\usepackage[utf8]{inputenc}, \\usepackage[T1]{fontenc}, \\usepackage{amsmath}, and \\begin{document} ... \\end{document}.\n"
                "4. Fix obvious typos, punctuation, and grammar errors in the provided text, but DO NOT change the style, tone, or overall meaning.\n"
                "5. Format plain text equations into proper LaTeX math mode environments (e.g., $...$ or \\begin{equation})."
    )
    
    if state["iteration"] > 0 and state["error"]:
        prompt_text = (
            f"Your previous LaTeX code failed to compile. Please fix it.\n\n"
            f"Compilation Error Log:\n{state['error']}\n\n"
            f"Original text to process:\n{state['user_input']}\n\n"
            f"Your previous broken code:\n{state['latex_code']}"
        )
    else:
        prompt_text = f"Process this text into a complete LaTeX document:\n\n{state['user_input']}"
        
    user_prompt = HumanMessage(content=prompt_text)
    response = llm.invoke([sys_prompt, user_prompt])
    raw_content = response.content
    
    # Extract filename from the first line comment
    filename_match = re.search(r'%\s*filename:\s*([a-zA-Z0-9_]+)', raw_content, re.IGNORECASE)
    filename = filename_match.group(1) if filename_match else "document"
    
    return {
        "latex_code": raw_content,
        "filename": filename,
        "iteration": state["iteration"] + 1
    }


def compile_pdf_node(state: State) -> dict:
    result_msg = compile_latex_to_pdf.invoke({
        "latex_content": state["latex_code"], 
        "filename": state["filename"]
    })
    
    if "Compilation Error" in result_msg:
        return {"error": result_msg, "pdf_path": ""}
    else:
        return {"error": "", "pdf_path": result_msg.replace("Success! PDF generated at ", "")}


def route_compilation(state: State) -> str:
    if not state.get("error"):
        return END
    
    if state["iteration"] >= 3:
        return END
        
    print(f"\n[!] Compilation failed. LLM is attempting to fix the code (Iteration {state['iteration']})...")
    return "generate_latex"


def build_latex_graph():
    builder = StateGraph(State)
    
    builder.add_node("generate_latex", generate_latex_node)
    builder.add_node("compile_pdf", compile_pdf_node)
    
    builder.add_edge(START, "generate_latex")
    builder.add_edge("generate_latex", "compile_pdf")
    builder.add_conditional_edges("compile_pdf", route_compilation)
    
    return builder.compile()


if __name__ == "__main__":
    app = build_latex_graph()
    
    print("Paste the text (or rough equations) to convert into a PDF (press Enter to submit):")
    user_text = input("> ")
    
    initial_state = {"user_input": user_text, "latex_code": "", "filename": "", "pdf_path": "", "error": "", "iteration": 0}
    
    print("\nGenerating code and checking compilation...")
    final_state = app.invoke(initial_state)
    
    if final_state.get("error"):
        print("\nEncountered an issue the LLM could not resolve within 3 iterations:")
        print(final_state["error"])
    else:
        print("\nSuccess!")
        print(f"Your file is located at: {final_state['pdf_path']}")
