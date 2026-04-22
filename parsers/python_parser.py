"""
Python parser using tree-sitter.
Extracts functions, classes, imports from .py files.
"""

from typing import List, Optional
import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from models import FileInfo, FunctionInfo, ClassInfo, ImportInfo, ParameterInfo
from parsers.base_parser import BaseParser

PY_LANGUAGE = Language(tspython.language())


class PythonParser(BaseParser):

    def __init__(self):
        self.parser = Parser(PY_LANGUAGE)

    def parse(self, file_info: FileInfo) -> FileInfo:
        try:
            source = self._read_source(file_info.absolute_path)
            tree = self.parser.parse(source)

            file_info.functions = self.extract_functions(source, tree)
            file_info.classes   = self.extract_classes(source, tree)
            file_info.imports   = self.extract_imports(source, tree)


            class_map = {c.name: c for c in file_info.classes}
            for func in file_info.functions:
                if func.is_method and func.class_name and func.class_name in class_map:
                    class_map[func.class_name].methods.append(func.name)

        except Exception as e:
            file_info.parse_error = str(e)

        return file_info



    def extract_functions(self, source: bytes, tree) -> List[FunctionInfo]:
        functions = []
        self._walk_functions(tree.root_node, source, functions, parent_class=None)
        return functions

    def _walk_functions(self, node, source: bytes, results: list, parent_class: Optional[str]):
        for child in node.children:
            if child.type == "function_definition":
                func = self._parse_function(child, source, parent_class)
                if func:
                    results.append(func)

                self._walk_functions(child, source, results, parent_class)

            elif child.type == "class_definition":
                class_name = self._get_child_text(child, "identifier", source)
                body = self._get_child_by_type(child, "block")
                if body:
                    self._walk_functions(body, source, results, parent_class=class_name)
            else:
                self._walk_functions(child, source, results, parent_class)

    def _parse_function(self, node, source: bytes, parent_class: Optional[str]) -> Optional[FunctionInfo]:
        name_node = self._get_child_by_type(node, "identifier")
        if not name_node:
            return None

        name = self._get_node_text(name_node, source)
        params = self._extract_parameters(node, source)
        return_type = self._extract_return_type(node, source)
        calls = self._extract_calls(node, source)

        return FunctionInfo(
            name=name,
            file_path="",
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            parameters=params,
            return_type=return_type,
            calls=calls,
            is_method=parent_class is not None,
            class_name=parent_class,
        )

    def _extract_parameters(self, func_node, source: bytes) -> List[ParameterInfo]:
        params = []
        params_node = self._get_child_by_type(func_node, "parameters")
        if not params_node:
            return params

        for child in params_node.children:
            if child.type == "identifier":
                name = self._get_node_text(child, source)
                if name != "self" and name != "cls":
                    params.append(ParameterInfo(name=name))
            elif child.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
                name_node = self._get_child_by_type(child, "identifier")
                type_node = self._get_child_by_type(child, "type")
                if name_node:
                    name = self._get_node_text(name_node, source)
                    type_hint = self._get_node_text(type_node, source) if type_node else None
                    if name not in ("self", "cls"):
                        params.append(ParameterInfo(name=name, type_hint=type_hint))

        return params

    def _extract_return_type(self, func_node, source: bytes) -> Optional[str]:
        for child in func_node.children:
            if child.type == "type":
                return self._get_node_text(child, source)
        return None

    def _extract_calls(self, func_node, source: bytes) -> List[str]:
        calls = []
        self._collect_calls(func_node, source, calls)
        return list(set(calls))

    def _collect_calls(self, node, source: bytes, calls: list):
        if node.type == "call":
            func_node = node.children[0] if node.children else None
            if func_node:
                if func_node.type == "identifier":
                    calls.append(self._get_node_text(func_node, source))
                elif func_node.type == "attribute":
                    attr = self._get_child_by_type(func_node, "identifier")
                    if attr:
                        calls.append(self._get_node_text(attr, source))
        for child in node.children:
            self._collect_calls(child, source, calls)



    def extract_classes(self, source: bytes, tree) -> List[ClassInfo]:
        classes = []
        for node in tree.root_node.children:
            if node.type == "class_definition":
                cls = self._parse_class(node, source)
                if cls:
                    classes.append(cls)
        return classes

    def _parse_class(self, node, source: bytes) -> Optional[ClassInfo]:
        name_node = self._get_child_by_type(node, "identifier")
        if not name_node:
            return None

        name = self._get_node_text(name_node, source)
        base_classes = self._extract_base_classes(node, source)

        return ClassInfo(
            name=name,
            file_path="",
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            base_classes=base_classes,
        )

    def _extract_base_classes(self, class_node, source: bytes) -> List[str]:
        bases = []
        args_node = self._get_child_by_type(class_node, "argument_list")
        if args_node:
            for child in args_node.children:
                if child.type == "identifier":
                    bases.append(self._get_node_text(child, source))
        return bases



    def extract_imports(self, source: bytes, tree) -> List[ImportInfo]:
        imports = []
        for node in tree.root_node.children:
            if node.type == "import_statement":
                raw = self._get_node_text(node, source)
                imports.append(ImportInfo(raw=raw, is_local=False))
            elif node.type == "import_from_statement":
                raw = self._get_node_text(node, source)
                module_node = self._get_child_by_type(node, "dotted_name")
                module = self._get_node_text(module_node, source) if module_node else None
                is_local = raw.startswith("from .") or raw.startswith("from ..")
                imports.append(ImportInfo(raw=raw, module=module, is_local=is_local))
        return imports



    def _get_child_by_type(self, node, type_name: str):
        for child in node.children:
            if child.type == type_name:
                return child
        return None

    def _get_child_text(self, node, type_name: str, source: bytes) -> Optional[str]:
        child = self._get_child_by_type(node, type_name)
        return self._get_node_text(child, source) if child else None
