# Lessons Learned While Experimenting with Windsurf, Claude Sonnet 3.7, and Gemini 2.5 Pro
This guide outlines practical tips and best practices for working effectively with LLM-based coding assistants. These insights are drawn from hands-on experience building Stridely and experimenting with models like Windsurf, Claude Sonnet 3.7, and Gemini 2.5 Pro.

## Git Is Your Best Friend
Large Language Models (LLMs) are excellent at generating large volumes of code quickly. However, they can unintentionally modify existing logic or introduce regressions. Robust version control is essential.

### Best practices:

- Always commit a working state before prompting an LLM for new features or refactoring.
- Use git diff to review suggested changes before merging them.
- Keep LLM-generated code in untracked files when experimenting.
- Use feature branches to isolate experimental work.
- Be ready to revert if things go sideways.

## Start with a Project Roadmap
LLMs tend to wander from the core objective, especially when left unguided. They often implement premature features or shift direction unexpectedly. Writing a roadmap helps anchor the generation process.

### Tips:

- Define your MVP features clearly before adding “nice-to-haves.”
- Break down complex features into smaller, manageable components.
- Let the LLM help you brainstorm the roadmap — it’s often surprisingly effective.
- Document architectural decisions to stay aligned over time.

## Define Code Generation Guidelines Upfront
To get maintainable, consistent output from an LLM, you need to explicitly specify your expectations. This includes formatting, naming conventions, structure, and comment style.

## Explore AI Agents with Desktop/Browser Interaction Capabilities
*   **Context:** Developing web GUIs and applications often involves repetitive manual checks like inspecting network logs, verifying DOM elements, or copying error messages from browser consoles back to the AI.
*   **Hypothesis/Untapped Potential:** While not extensively used in the current project, there appears to be significant potential in AI agents that can directly interact with the desktop environment (e.g., open developer tools, read console output, inspect network requests). Such capabilities could drastically reduce manual effort and speed up the development cycle, allowing the developer to focus on higher-level problem-solving. This is a promising area for future workflow enhancements.

### Recommendations:

- Establish coding standards in a shared document (e.g., docs/code_guidelines.md).
- Be consistent with project structure and style.
- Use tools like ruff, mypy, pre-commit, and linters to enforce consistency.
- Refine guidelines as needed when output quality is lacking.

## LLMs Are Great at Writing Code—But Not at Fixing It
LLMs can generate both implementation and test code — but they’re often unreliable at debugging. Prompting them to find and fix issues frequently leads to broken or inconsistent behavior.
### Better strategy:

- Use static analysis tools (mypy, linters) to catch type and syntax issues early.
- Debug with pdb to get hands-on insight — it's usually faster and more reliable.
- Manually verify functionality before merging LLM-suggested changes.
- Avoid prompting the model to "guess" what’s wrong — it often leads to more confusion than clarity.

## Communicate Clearly with the Model
The quality of an LLM’s output is directly tied to the quality of your prompt. Clear, specific instructions help prevent misunderstandings.
### Guidelines:

- Be specific and direct with your requests.
- Provide relevant context, such as prior design choices or goals.
- Point out known errors rather than asking the LLM to find them.
- Break tasks into smaller, well-defined steps for better control.

## Collaborate Iteratively
Treat the LLM as a capable partner — not an all-knowing oracle. Use it to assist in exploration, but apply your judgment to refine its suggestions.
### Working style:

- Start simple and iterate. Don't try to build everything in one go.
- Continuously review and refine generated code.
- Combine your domain knowledge with the LLM’s capabilities.
- Provide feedback (even conversationally) to guide future interactions.

## Test Thoroughly
Just like human-written code, LLM-generated code must be tested rigorously.
### Testing strategy:

- Always review the logic and functionality manually.
- Ask the LLM to write tests, but validate them carefully.
- Test edge cases and error conditions explicitly.
- Incorporate automated testing into your development pipeline.

## Manage Complexity and Scope
LLMs often introduce unnecessary complexity. Stay focused on core functionality and defer advanced features until the foundation is solid.
### Advice:

- Be mindful of “feature creep” — resist the urge to implement everything at once.
- Maintain a clear separation of concerns in your architecture.
- Keep documentation in sync with code and decisions.
- Prioritize readability and maintainability over novelty.

## LLM Collaboration Nuances

When working with LLMs (e.g., Gemini 2.5 Pro, Claude 3 Sonnet) on intricate code modifications, such as adjusting specific filter conditions within a complex method, they may sometimes propose larger-scale refactors rather than small, surgical changes. These expansive refactors can often be perplexing, creating unnecessary mess and potentially breaking existing functionality. For such fine-grained adjustments, it is frequently more effective to leverage one's own coding expertise to make the precise change, or to guide the LLM with very specific, constrained instructions. This highlights the importance of human oversight and a critical approach when pair-programming with AI, especially for delicate modifications in an existing codebase.

**Django Forms and LLMs:** Generating well-functioning Django forms with LLMs can be particularly frustrating. It usually leads to breaking a ton of stuff and circulating in a vicious circle. This highlights a specific area where LLM assistance requires extreme caution and often more manual intervention.

## Final Thoughts
Effective LLM-assisted development is a skill — one that improves with experience. By applying structured workflows, clear communication, and a collaborative mindset, you can get the most out of tools like Claude Sonnet, Gemini, and Windsurf.

Co-authored with a free-tier OpenAI ChatGPT.
