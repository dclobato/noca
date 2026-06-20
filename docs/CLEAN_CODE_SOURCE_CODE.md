# Practical File Size Ranges in Python

Based on Clean Code principles (from Robert C. Martin), adapted to Python:

- 50–150 lines (0.05–0.15 KLOC) → ideal, very readable
- 150–300 lines → still healthy
- 300–500 lines → acceptable, but watch for growth
- 500–800 lines → borderline, likely needs refactoring
- 800+ lines (~0.8+ KLOC) → strong code smell
- 1000+ lines (1+ KLOC) → almost certainly violating the Single Responsibility Principle

## Why Python Pushes Toward Smaller Files

Python encourages:

- Short, expressive functions
- Fewer boilerplate constructs
- Flat, readable structures (see “import this” → the Zen of Python)

So large files tend to mean:

- Too many responsibilities
- Weak modularization
- Poor separation of concerns

## What a “Healthy” Python File Looks Like

A typical clean Python module might include:

- 1–3 classes or
- A small group of related functions
- Clear top-level purpose (e.g., user_service.py, payment_validator.py)

If you see:

- Many unrelated functions
- Multiple domains mixed together
- Long procedural flows

...it’s time to split.

## Common Python-Specific Exceptions

Some files are naturally larger, and that’s okay:

- Django models/views files (though even here, splitting is encouraged)
- Configuration modules
- Auto-generated code
- Data schemas (e.g., Pydantic models)


## Python Smells That Signal Oversized Files

Instead of counting lines, watch for these:

- "Scroll fatigue" (you keep paging down to understand context)
- Functions referencing many unrelated concepts
- Frequent need to jump around the file
- Hard-to-name file ("utils.py" is a classic offender)

## A Better Rule Than KLOC in Python

Ask:

> "Can a human understand this module’s purpose and structure in ~30–60 seconds?"

If not, break it apart.
