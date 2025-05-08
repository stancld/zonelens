# Lessons Learned While Experimenting with Windsurf, Claude Sonnet 3.7, and Gemini 2.5 Pro

## Git Is Your Best Friend
Large Language Models (LLMs) are excellent at generating large volumes of code quickly. However, they can also unintentionally modify existing logic or introduce regressions when proposing changes. Robust version control is essential. My preferred workflow involves tracking all file versions before prompting the LLM. This way, I can isolate and review the changes separately — often keeping LLM-generated outputs in untracked files for clarity and safety.

## Start with a Project Roadmap
LLMs tend to wander from the core objective, especially if left unguided. They often jump ahead and implement extra features before they’re needed. Writing a clear project roadmap or scope before you begin helps anchor the conversation and keeps the generation process on track. Tip: It’s surprisingly productive (and fun) to let the LLM help draft this roadmap with you.

## Define Code Generation Guidelines Upfront
To get consistent, maintainable output from LLMs, it's helpful to explicitly state your expectations regarding code style, formatting, in-line comments, naming conventions, etc. Clear instructions tend to significantly improve the relevance and readability of the generated code.

Co-authored by a free-tier OpenAI ChatGPT.
