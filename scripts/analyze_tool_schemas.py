#!/usr/bin/env python3
"""
Tool Response Schema Analyzer

This script analyzes Claude Code tool responses from log files to generate
comprehensive documentation of available parameters for each tool.

Usage:
    python scripts/analyze_tool_schemas.py [options]

Options:
    --log-file PATH     Path to log file (default: telegram-claude-bot.log)
    --output PATH       Output file path (default: tool_schemas.json)
    --update            Update existing schema file instead of overwriting
    --verbose           Enable verbose output
    --tool TOOL_NAME    Analyze only specific tool (can be used multiple times)
"""

import argparse
import json
import sys

from collections import defaultdict
from datetime import datetime
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

    def analyze_log_file(
        self, log_file_path: Path, target_tools: Optional[Set[str]] = None
    ):
        """Analyze log file for tool response patterns."""
        self.log(f"Analyzing log file: {log_file_path}")

        if not log_file_path.exists():
            raise FileNotFoundError(f"Log file not found: {log_file_path}")

        with open(log_file_path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                try:
                    self._process_log_line(line.strip(), line_num, target_tools)
                except Exception as e:
                    if self.verbose:
                        print(f"Warning: Error processing line {line_num}: {e}")

    def _process_log_line(
        self, line: str, line_num: int, target_tools: Optional[Set[str]]
    ):
        """Process a single log line for tool data."""
        try:
            # Parse the JSON log line
            log_data = json.loads(line)
        except json.JSONDecodeError:
            return

        # Look for PostToolUse hook processing lines
        if log_data.get("event") != "Processing PostToolUse hook":
            return

        tool_name = log_data.get("tool_name")
        if not tool_name:
            return

        # Skip if we're filtering for specific tools
        if target_tools and tool_name not in target_tools:
            return

        self.log(f"Processing {tool_name} at line {line_num}")

        # Extract tool_input
        tool_input = log_data.get("tool_input_truncated")
        if tool_input:
            self._analyze_parameters(tool_name, "input", tool_input)

        # Extract tool_response
        tool_response = log_data.get("tool_response_truncated")
        if tool_response:
            self._analyze_parameters(tool_name, "response", tool_response)

        # Update usage stats
        self.tool_schemas[tool_name]["usage_count"] += 1
        self.tool_schemas[tool_name]["last_seen"] = datetime.now().isoformat()

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
                element_types = {self._get_value_type(item) for item in value[:3]}
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
                existing_schema.setdefault("tools", {})[tool_name] = new_tool_data

        # Update metadata
        existing_schema["metadata"] = new_schema["metadata"]

        return existing_schema


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Claude Code tool response schemas"
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default="telegram-claude-bot.log",
        help="Path to log file",
    )
    parser.add_argument(
        "--output", type=Path, default="tool_schemas.json", help="Output file path"
    )
    parser.add_argument(
        "--update", action="store_true", help="Update existing schema file"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
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
        # Analyze log file
        analyzer.analyze_log_file(args.log_file, target_tools)

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
