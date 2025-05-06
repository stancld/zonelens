# Strava Zones Coding Guidelines

(Used for LLM instructions)

## General Guidelines 📝

- Generate concise code that accomplishes the task with minimal verbosity
- Focus on clear, well-named functions rather than redundant comments
- Keep line length to a maximum of 99 characters
- Implementation should be macOS/Linux compatible only
- Manage credentials and secrets through environment variables only
- Never generate components in advance – only implement features when explicitly requested
- Complete and test each component fully before moving to the next

## Python Conventions

### Indentation and Formatting

- Use **tabs** for indentation (not spaces)
- Use ruff for formatting and linting
- Sort imports with isort (handled by ruff)

### Type Annotations

- Use modern Python 3.11+ type syntax
- Always include return type annotations in function definitions
- Include type annotations for all function parameters

### ✅ DO: Use modern Python type syntax

Use the pipe operator (`|`) for union types in Python 3.10+:

```python
def process_input(value: str | int | None = None):
    # Function implementation
```

### ❌ DON'T: Use Union from typing

```python
from typing import Union

# Don't do this
def process_input(value: Union[str, int, None] = None):
    # Function implementation
```

### Documentation

- Include proper docstrings in numpy style
- Document complex functions with parameter and return type descriptions
- Add inline comments for non-obvious logic
- **Do not** include type information in docstrings since it duplicates type annotations
- Always use `from __future__ import annotations` at the top of every Python file

### ✅ DO: Use proper docstrings without redundant type information

```python
from __future__ import annotations

def calculate_pace(distance_meters: float, duration_seconds: int) -> float | None:
    """Calculate the running pace in minutes per kilometer.

    Args:
        distance_meters: The distance traveled
        duration_seconds: The time taken

    Returns:
        Pace in seconds per kilometer or None if distance is invalid
    """
    if not distance_meters or distance_meters <= 0:
        return None

    return duration_seconds / (distance_meters / 1000)
```

### ❌ DON'T: Duplicate type information in docstrings

```python
def calculate_pace(distance_meters: float, duration_seconds: int) -> float | None:
    """Calculate the running pace in minutes per kilometer.

    Args:
        distance_meters (float): The distance traveled
        duration_seconds (int): The time taken

    Returns:
        float | None: Pace in seconds per kilometer or None if distance is invalid
    """
    # Implementation
```

## FastAPI Best Practices

### ✅ DO: Define dependencies at module level

Always define FastAPI dependencies at the module level and reference them in function parameters:

```python
# Define dependencies at module level
db_dependency = Depends(get_db)
current_user_dependency = Depends(get_current_user)

@router.get("/items")
async def get_items(
    db: AsyncSession = db_dependency,
    current_user: User = current_user_dependency
):
    # Function implementation
```

### ❌ DON'T: Call Depends() in function parameter defaults

Avoid calling `Depends()` directly in function parameter defaults:

```python
# Don't do this! Will cause linting error B008
@router.get("/items")
async def get_items(
    db: AsyncSession = Depends(get_db),  # B008 violation
    current_user: User = Depends(get_current_user)  # B008 violation
):
    # Function implementation
```

This causes the B008 linting error: "Do not perform function call in argument defaults".

## Error Handling

### ✅ DO: Use `raise ... from err` in exception blocks

Always use `raise ... from err` syntax in exception blocks to maintain the exception chain:

```python
try:
    # Something that might fail
    process_data()
except ValueError as err:
    raise HTTPException(status_code=400, detail="Invalid data") from err
```

### ❌ DON'T: Drop the original exception context

```python
try:
    # Something that might fail
    process_data()
except ValueError:
    # Don't do this! Drops the original exception context
    raise HTTPException(status_code=400, detail="Invalid data")
```

## Logging

### ✅ DO: Use the `logging` module for output

Prefer the standard `logging` module over `print()` for application output, status messages, and debugging information.

**Why?**
- **Flexibility:** Allows configuring different output destinations (console, file, network).
- **Levels:** Supports different severity levels (DEBUG, INFO, WARNING, ERROR, CRITICAL).
- **Control:** Enables fine-grained control over which messages are shown based on configuration.

**Example:**

```python
import logging

# Configure basic logging (e.g., in your app setup)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

logger = logging.getLogger(__name__)

# --- Usage ---
def process_data(data):
    logger.info("Starting data processing for %s items", len(data))
    try:
        # ... processing logic ...
        result = data.do_something()
        logger.debug("Intermediate result: %s", result) # Only shown if level is DEBUG
    except Exception as e:
        logger.error("Data processing failed: %s", e, exc_info=True) # Include traceback
        raise
    logger.info("Data processing finished successfully.")
```

### ❌ DON'T: Use `print()` for application output

Avoid using `print()` for logging, debugging, or status messages in application code. Reserve `print()` primarily for command-line scripts intended for direct user interaction or very specific debugging scenarios where logging setup is unavailable or overly complex.

```python
# Don't do this in application modules:
print("Processing started...")
# ...
print(f"Error processing item {item_id}: {error_message}")
```

## Project Structure

- Keep related components in appropriate modules
- Use semantic module and function naming
- Organize code with clear separation of concerns

## Dependency Management

- Do not include version numbers in `pyproject.toml` dependencies section
- Use uv for dependency management and installation

## Code Modifications

- Never remove existing code without explicit permission
- When making changes, always explain the rationale
- Security considerations must not be compromised
- Consider backward compatibility when modifying interfaces
- Before committing code changes, review with the following in mind:
  - Question any code that looks suspicious or odd in a given context
  - Avoid using deprecated syntax or methods; use modern alternatives
  - Simplify code where possible without compromising readability
  - Follow standard code paradigms such as DRY (Don't Repeat Yourself) and YAGNI (You Aren't Gonna Need It)

## Pre-Commit Hooks

Always run pre-commit hooks before committing:

```bash
pre-commit run --all-files
```

## Additional Resources
- [Ruff Documentation](https://beta.ruff.rs/docs/)
- [Python Type Hints](https://docs.python.org/3/library/typing.html)
