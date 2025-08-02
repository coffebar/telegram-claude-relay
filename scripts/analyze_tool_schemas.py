#!/usr/bin/env python3
"""
Tool Response Schema Analyzer

This script analyzes Claude Code tool responses from Claude's JSONL files to generate
comprehensive documentation of available parameters for each tool.

Usage:
    python scripts/analyze_tool_schemas.py [options]

Options:
    --claude-dir PATH   Path to Claude projects directory (default: ~/.claude/projects)
    --days INTEGER      Number of days back to look for JSONL files (default: 7)
    --output PATH       Output file path (default: tool_schemas.json)
    --update            Update existing schema file instead of overwriting
    --verbose           Enable verbose output
    --tool TOOL_NAME    Analyze only specific tool (can be used multiple times)
"""

import argparse
import json
import os
import sys

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class ToolSchemaAnalyzer:
    """Analyzes tool responses to extract parameter schemas."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.tool_schemas = defaultdict(
            lambda: {
                "input_parameters": defaultdict(set),
                "response_parameters": defaultdict(set),
                "input_examples": defaultdict(list),
                "response_examples": defaultdict(list),
                "usage_count": 0,
                "last_seen": None,
            }
        )

    def log(self, message: str):
        """Print verbose log message."""
        if self.verbose:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def analyze_jsonl_files(
        self, claude_dir: Path, days_back: int, target_tools: Optional[Set[str]] = None
    ):
        """Analyze Claude JSONL files for tool response patterns."""
        self.log(f"Analyzing Claude JSONL files in: {claude_dir}")

        if not claude_dir.exists():
            raise FileNotFoundError(
                f"Claude directory not found: {claude_dir}")

        # Find JSONL files modified in the last N days
        cutoff_date = datetime.now() - timedelta(days=days_back)
        jsonl_files = []

        for root, dirs, files in os.walk(claude_dir):
            for file in files:
                if file.endswith('.jsonl'):
                    file_path = Path(root) / file
                    try:
                        if datetime.fromtimestamp(file_path.stat().st_mtime) >= cutoff_date:
                            jsonl_files.append(file_path)
                    except (OSError, ValueError):
                        continue

        self.log(
            f"Found {len(jsonl_files)} JSONL files modified in last {days_back} days")

        # Track tool use/result pairs
        tool_use_map = {}  # Map tool_use_id to tool_use data

        for jsonl_file in jsonl_files:
            self.log(f"Processing: {jsonl_file}")
            try:
                self._process_jsonl_file(
                    jsonl_file, target_tools, tool_use_map)
            except Exception as e:
                if self.verbose:
                    print(f"Warning: Error processing file {jsonl_file}: {e}")

    def _process_jsonl_file(
        self, jsonl_file: Path, target_tools: Optional[Set[str]], tool_use_map: Dict[str, Any]
    ):
        """Process a single JSONL file for tool data."""
        with open(jsonl_file, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                try:
                    self._process_jsonl_line(
                        line.strip(), line_num, target_tools, tool_use_map)
                except Exception as e:
                    if self.verbose:
                        print(
                            f"Warning: Error processing line {line_num} in {jsonl_file}: {e}")

    def _process_jsonl_line(
        self, line: str, line_num: int, target_tools: Optional[Set[str]], tool_use_map: Dict[str, Any]
    ):
        """Process a single JSONL line for tool data."""
        try:
            # Parse the JSON line
            data = json.loads(line)
        except json.JSONDecodeError:
            return

        # Skip non-assistant and non-user messages
        if data.get("type") not in ["assistant", "user"]:
            return

        message = data.get("message", {})
        content = message.get("content", [])

        if not isinstance(content, list):
            return

        # Process tool_use and tool_result content
        for item in content:
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")

            if item_type == "tool_use":
                # Store tool use for later matching with results
                tool_use_id = item.get("id")
                tool_name = item.get("name")
                tool_input = item.get("input", {})

                if tool_use_id and tool_name:
                    # Skip if we're filtering for specific tools
                    if target_tools and tool_name not in target_tools:
                        continue

                    tool_use_map[tool_use_id] = {
                        "name": tool_name,
                        "input": tool_input,
                        "line_num": line_num
                    }

                    self.log(
                        f"Found tool_use {tool_name} with ID {tool_use_id} at line {line_num}")

                    # Analyze input parameters
                    self._analyze_parameters(tool_name, "input", tool_input)

            elif item_type == "tool_result":
                # Match with previously stored tool_use
                tool_use_id = item.get("tool_use_id")

                if tool_use_id in tool_use_map:
                    tool_use_data = tool_use_map[tool_use_id]
                    tool_name = tool_use_data["name"]

                    self.log(
                        f"Found tool_result for {tool_name} (ID: {tool_use_id}) at line {line_num}")

                    # Analyze result from content field
                    result_content = item.get("content")
                    if result_content:
                        self._analyze_parameters(tool_name, "response", {
                                                 "content": result_content})

                    # Also analyze toolUseResult if present (has more structured data)
                    tool_use_result = data.get("toolUseResult", {})
                    if tool_use_result:
                        self._analyze_parameters(
                            tool_name, "response", tool_use_result)

                    # Update usage stats
                    self.tool_schemas[tool_name]["usage_count"] += 1
                    self.tool_schemas[tool_name]["last_seen"] = datetime.now(
                    ).isoformat()

                    # Remove from map to avoid reprocessing
                    del tool_use_map[tool_use_id]

    def _analyze_parameters(self, tool_name: str, param_type: str, data: Any):
        """Analyze parameters from tool input/response data."""
        if not isinstance(data, dict):
            return

        schema_key = f"{param_type}_parameters"
        examples_key = f"{param_type}_examples"

        for key, value in data.items():
            # Record parameter existence and type
            value_type = self._get_value_type(value)
            self.tool_schemas[tool_name][schema_key][key].add(value_type)

            # Store example (limited to avoid memory issues)
            examples = self.tool_schemas[tool_name][examples_key][key]
            if len(examples) < 3:  # Limit examples per parameter
                examples.append(
                    {
                        "type": value_type,
                        "value": self._sanitize_example_value(value),
                        "size": len(str(value)) if value else 0,
                    }
                )

    def _get_value_type(self, value: Any) -> str:
        """Get simplified type description for a value."""
        if value is None:
            return "null"
        elif isinstance(value, bool):
            return "boolean"
        elif isinstance(value, int):
            return "integer"
        elif isinstance(value, float):
            return "number"
        elif isinstance(value, str):
            return "string"
        elif isinstance(value, list):
            if value:
                element_types = {self._get_value_type(
                    item) for item in value[:3]}
                if len(element_types) == 1:
                    return f"array<{element_types.pop()}>"
                else:
                    return "array<mixed>"
            return "array<empty>"
        elif isinstance(value, dict):
            return "object"
        else:
            return str(type(value).__name__)

    def _sanitize_example_value(self, value: Any, max_length: int = 100) -> Any:
        """Sanitize example values for documentation."""
        if isinstance(value, str):
            if len(value) > max_length:
                return value[:max_length] + "..."
            return value
        elif isinstance(value, (list, dict)):
            str_repr = str(value)
            if len(str_repr) > max_length:
                return str_repr[:max_length] + "..."
            return value
        else:
            return value

    def generate_schema(self) -> Dict[str, Any]:
        """Generate the final schema documentation."""
        schema = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "tools_analyzed": len(self.tool_schemas),
            },
            "tools": {},
        }

        for tool_name, tool_data in self.tool_schemas.items():
            # Convert sets to lists and determine optionality
            input_params = {}
            for param, types in tool_data["input_parameters"].items():
                # Determine if parameter is optional based on usage frequency
                is_optional = tool_data["usage_count"] > len(
                    tool_data["input_examples"].get(param, [])
                )
                param_types = list(types)

                input_params[param] = {
                    "type": param_types[0] if len(param_types) == 1 else param_types,
                    "optional": is_optional,
                }

            response_params = {}
            for param, types in tool_data["response_parameters"].items():
                # All response parameters are technically optional (tool might not return them)
                param_types = list(types)

                response_params[param] = {
                    "type": param_types[0] if len(param_types) == 1 else param_types,
                    "optional": True,
                }

            schema["tools"][tool_name] = {
                "input_parameters": input_params,
                "response_parameters": response_params,
            }

        return schema

    def _get_common_params(self, params: Dict[str, List[str]]) -> List[str]:
        """Get commonly used parameters (present in examples)."""
        return [param for param, types in params.items() if types]

    def update_existing_schema(self, existing_schema: Dict[str, Any]) -> Dict[str, Any]:
        """Update existing schema with new data."""
        new_schema = self.generate_schema()

        # Merge tool data
        for tool_name, new_tool_data in new_schema["tools"].items():
            if tool_name in existing_schema.get("tools", {}):
                existing_tool = existing_schema["tools"][tool_name]

                # Merge parameters
                for param_type in ["input_parameters", "response_parameters"]:
                    existing_params = existing_tool.get(param_type, {})
                    new_params = new_tool_data[param_type]

                    for param, param_info in new_params.items():
                        if param in existing_params:
                            # Merge types
                            existing_type = existing_params[param]["type"]
                            new_type = param_info["type"]

                            if isinstance(existing_type, list):
                                existing_types = set(existing_type)
                            else:
                                existing_types = {existing_type}

                            if isinstance(new_type, list):
                                existing_types.update(new_type)
                            else:
                                existing_types.add(new_type)

                            merged_types = list(existing_types)
                            existing_params[param] = {
                                "type": (
                                    merged_types[0]
                                    if len(merged_types) == 1
                                    else merged_types
                                ),
                                "optional": existing_params[param]["optional"]
                                and param_info["optional"],
                            }
                        else:
                            existing_params[param] = param_info
            else:
                # New tool
                existing_schema.setdefault("tools", {})[
                    tool_name] = new_tool_data

        # Update metadata
        existing_schema["metadata"] = new_schema["metadata"]

        return existing_schema


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Claude Code tool response schemas from JSONL files"
    )
    parser.add_argument(
        "--claude-dir",
        type=Path,
        default=Path.home() / ".claude" / "projects",
        help="Path to Claude projects directory (default: ~/.claude/projects)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days back to look for JSONL files (default: 7)",
    )
    parser.add_argument(
        "--output", type=Path, default="tool_schemas.json", help="Output file path"
    )
    parser.add_argument(
        "--update", action="store_true", help="Update existing schema file"
    )
    parser.add_argument("--verbose", action="store_true",
                        help="Enable verbose output")
    parser.add_argument(
        "--tool", action="append", dest="tools", help="Analyze only specific tools"
    )

    args = parser.parse_args()

    # Create output directory if needed
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Initialize analyzer
    analyzer = ToolSchemaAnalyzer(verbose=args.verbose)

    # Convert tool list to set if provided
    target_tools = set(args.tools) if args.tools else None

    try:
        # Analyze JSONL files
        analyzer.analyze_jsonl_files(args.claude_dir, args.days, target_tools)

        # Generate or update schema
        if args.update and args.output.exists():
            print(f"Updating existing schema: {args.output}")
            with open(args.output) as f:
                existing_schema = json.load(f)
            final_schema = analyzer.update_existing_schema(existing_schema)
        else:
            print(f"Generating new schema: {args.output}")
            final_schema = analyzer.generate_schema()

        # Write output
        with open(args.output, "w") as f:
            json.dump(final_schema, f, indent=2, sort_keys=True)

        # Print summary
        print("\nAnalysis complete!")
        print(f"Tools analyzed: {final_schema['metadata']['tools_analyzed']}")
        print(f"Output written to: {args.output}")

        if args.verbose:
            print("\nTool summary:")
            for tool_name, tool_data in final_schema["tools"].items():
                input_count = len(tool_data["input_parameters"])
                response_count = len(tool_data["response_parameters"])
                print(
                    f"  {tool_name}: {input_count} input params, {response_count} response params"
                )

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
