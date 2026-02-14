"""Tests for example modules in the examples/ directory."""

from __future__ import annotations

import importlib.util
import pathlib

import yaml
from pydantic import BaseModel

from apcore.context import Context
from apcore.module import ModuleAnnotations, ModuleExample

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def _load_example_module(relative_path: str):
    """Load a Python module from a path relative to PROJECT_ROOT using importlib."""
    full_path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(full_path.stem, str(full_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- GreetModule Tests ---


class TestGreetModule:
    """Test: GreetModule instantiable with required attributes."""

    def test_greet_module_has_input_schema(self):
        mod = _load_example_module("examples/modules/greet.py")
        instance = mod.GreetModule()
        assert hasattr(instance, "input_schema")
        assert issubclass(instance.input_schema, BaseModel)

    def test_greet_module_has_output_schema(self):
        mod = _load_example_module("examples/modules/greet.py")
        instance = mod.GreetModule()
        assert hasattr(instance, "output_schema")
        assert issubclass(instance.output_schema, BaseModel)

    def test_greet_module_has_description(self):
        mod = _load_example_module("examples/modules/greet.py")
        instance = mod.GreetModule()
        assert isinstance(instance.description, str)
        assert len(instance.description) > 0

    def test_greet_module_execute_returns_correct_greeting(self):
        mod = _load_example_module("examples/modules/greet.py")
        instance = mod.GreetModule()
        ctx = Context.create()
        result = instance.execute({"name": "Alice"}, ctx)
        assert result == {"message": "Hello, Alice!"}


# --- SendEmailModule Tests ---


class TestSendEmailModule:
    """Test: SendEmailModule has annotations, tags, version, examples."""

    def test_send_email_has_annotations(self):
        mod = _load_example_module("examples/modules/send_email.py")
        instance = mod.SendEmailModule()
        assert isinstance(instance.annotations, ModuleAnnotations)
        assert instance.annotations.destructive is True
        assert instance.annotations.idempotent is False

    def test_send_email_has_tags(self):
        mod = _load_example_module("examples/modules/send_email.py")
        instance = mod.SendEmailModule()
        assert isinstance(instance.tags, list)
        assert len(instance.tags) > 0

    def test_send_email_has_version(self):
        mod = _load_example_module("examples/modules/send_email.py")
        instance = mod.SendEmailModule()
        assert isinstance(instance.version, str)

    def test_send_email_has_examples(self):
        mod = _load_example_module("examples/modules/send_email.py")
        instance = mod.SendEmailModule()
        assert isinstance(instance.examples, list)
        assert len(instance.examples) > 0
        assert isinstance(instance.examples[0], ModuleExample)

    def test_send_email_input_schema_has_sensitive_field(self):
        mod = _load_example_module("examples/modules/send_email.py")
        schema = mod.SendEmailInput.model_json_schema()
        api_key_props = schema["properties"]["api_key"]
        assert api_key_props.get("x-sensitive") is True

    def test_send_email_execute_returns_status(self):
        mod = _load_example_module("examples/modules/send_email.py")
        instance = mod.SendEmailModule()
        ctx = Context.create()
        child = ctx.child("send_email")
        result = instance.execute(
            {
                "to": "test@example.com",
                "subject": "Hi",
                "body": "Hello",
                "api_key": "sk-test",
            },
            child,
        )
        assert result["status"] == "sent"
        assert "message_id" in result


# --- GetUserModule Tests ---


class TestGetUserModule:
    """Test: GetUserModule has readonly and idempotent annotations."""

    def test_get_user_has_readonly_annotation(self):
        mod = _load_example_module("examples/modules/get_user.py")
        instance = mod.GetUserModule()
        assert instance.annotations.readonly is True

    def test_get_user_has_idempotent_annotation(self):
        mod = _load_example_module("examples/modules/get_user.py")
        instance = mod.GetUserModule()
        assert instance.annotations.idempotent is True

    def test_get_user_execute_returns_user_data(self):
        mod = _load_example_module("examples/modules/get_user.py")
        instance = mod.GetUserModule()
        ctx = Context.create()
        result = instance.execute({"user_id": "user-1"}, ctx)
        assert result == {"id": "user-1", "name": "Alice", "email": "alice@example.com"}


# --- Decorated Add Tests ---


class TestDecoratedAdd:
    """Test: decorated add function has apcore_module attribute."""

    def test_add_has_apcore_module_attribute(self):
        mod = _load_example_module("examples/modules/decorated_add.py")
        assert hasattr(mod.add, "apcore_module")

    def test_add_module_produces_correct_sum(self):
        mod = _load_example_module("examples/modules/decorated_add.py")
        fm = mod.add.apcore_module
        ctx = Context.create()
        result = fm.execute({"a": 2, "b": 3}, ctx)
        assert result == {"result": 5}


# --- Format Date Binding Tests ---


class TestFormatDateBinding:
    """Test: binding.yaml is valid YAML with required fields."""

    def test_binding_yaml_is_valid(self):
        binding_path = PROJECT_ROOT / "examples" / "bindings" / "format_date" / "binding.yaml"
        data = yaml.safe_load(binding_path.read_text())
        assert "bindings" in data
        assert isinstance(data["bindings"], list)
        assert len(data["bindings"]) >= 1
        entry = data["bindings"][0]
        assert "module_id" in entry
        assert "target" in entry

    def test_format_date_function_formats_dates(self):
        mod = _load_example_module("examples/bindings/format_date/format_date.py")
        result = mod.format_date_string("2024-01-15", "%B %d, %Y")
        assert result == {"formatted": "January 15, 2024"}
