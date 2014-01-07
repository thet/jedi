import os
import copy

from jedi import cache
from jedi import common
from jedi.parser import representation as pr
from jedi.common import source_to_unicode
from jedi import settings


def get_modules_containing_name(mods, name):
    """
    Search a name in the directories of modules.
    """
    def check_python_file(path):
        try:
            return cache.parser_cache[path].parser.module
        except KeyError:
            try:
                return check_fs(path)
            except IOError:
                return None

    def check_fs(path):
        with open(path) as f:
            source = source_to_unicode(f.read())
            if name in source:
                from jedi.evaluate import imports
                return imports.load_module(path, source)

    # skip non python modules
    mods = set(m for m in mods if m.path is None or m.path.endswith('.py'))
    mod_paths = set()
    for m in mods:
        mod_paths.add(m.path)
        yield m

    if settings.dynamic_params_for_other_modules:
        paths = set(settings.additional_dynamic_modules)
        for p in mod_paths:
            if p is not None:
                d = os.path.dirname(p)
                for entry in os.listdir(d):
                    if entry not in mod_paths:
                        if entry.endswith('.py'):
                            paths.add(d + os.path.sep + entry)

        for p in sorted(paths):
            # make testing easier, sort it - same results on every interpreter
            c = check_python_file(p)
            if c is not None and c not in mods:
                yield c


def fast_parent_copy(obj):
    """
    Much, much faster than copy.deepcopy, but just for certain elements.
    """
    new_elements = {}

    def recursion(obj):
        if isinstance(obj, pr.Statement):
            # Need to set _set_vars, otherwise the cache is not working
            # correctly, don't know why.
            obj.get_set_vars()

        new_obj = copy.copy(obj)
        new_elements[obj] = new_obj

        try:
            items = list(new_obj.__dict__.items())
        except AttributeError:
            # __dict__ not available, because of __slots__
            items = []

        before = ()
        for cls in new_obj.__class__.__mro__:
            with common.ignored(AttributeError):
                if before == cls.__slots__:
                    continue
                before = cls.__slots__
                items += [(n, getattr(new_obj, n)) for n in before]

        for key, value in items:
            # replace parent (first try _parent and then parent)
            if key in ['parent', '_parent'] and value is not None:
                if key == 'parent' and '_parent' in items:
                    # parent can be a property
                    continue
                with common.ignored(KeyError):
                    setattr(new_obj, key, new_elements[value])
            elif key in ['parent_function', 'use_as_parent', '_sub_module']:
                continue
            elif isinstance(value, list):
                setattr(new_obj, key, list_rec(value))
            elif isinstance(value, pr.Simple):
                setattr(new_obj, key, recursion(value))
        return new_obj

    def list_rec(list_obj):
        copied_list = list_obj[:]   # lists, tuples, strings, unicode
        for i, el in enumerate(copied_list):
            if isinstance(el, pr.Simple):
                copied_list[i] = recursion(el)
            elif isinstance(el, list):
                copied_list[i] = list_rec(el)
        return copied_list
    return recursion(obj)


def array_for_pos(stmt, pos, array_types=None):
    """Searches for the array and position of a tuple"""
    def search_array(arr, pos):
        if arr.type == 'dict':
            for stmt in arr.values + arr.keys:
                new_arr, index = array_for_pos(stmt, pos, array_types)
                if new_arr is not None:
                    return new_arr, index
        else:
            for i, stmt in enumerate(arr):
                new_arr, index = array_for_pos(stmt, pos, array_types)
                if new_arr is not None:
                    return new_arr, index
                if arr.start_pos < pos <= stmt.end_pos:
                    if not array_types or arr.type in array_types:
                        return arr, i
        if len(arr) == 0 and arr.start_pos < pos < arr.end_pos:
            if not array_types or arr.type in array_types:
                return arr, 0
        return None, 0

    def search_call(call, pos):
        arr, index = None, 0
        if call.next is not None:
            if isinstance(call.next, pr.Array):
                arr, index = search_array(call.next, pos)
            else:
                arr, index = search_call(call.next, pos)
        if not arr and call.execution is not None:
            arr, index = search_array(call.execution, pos)
        return arr, index

    if stmt.start_pos >= pos >= stmt.end_pos:
        return None, 0

    for command in stmt.expression_list():
        arr = None
        if isinstance(command, pr.Array):
            arr, index = search_array(command, pos)
        elif isinstance(command, pr.StatementElement):
            arr, index = search_call(command, pos)
        if arr is not None:
            return arr, index
    return None, 0


def search_call_signatures(stmt, pos):
    """
    Returns the function Call that matches the position before.
    """
    # some parts will of the statement will be removed
    stmt = fast_parent_copy(stmt)
    arr, index = array_for_pos(stmt, pos, [pr.Array.TUPLE, pr.Array.NOARRAY])
    if arr is not None and isinstance(arr.parent, pr.StatementElement):
        call = arr.parent
        while isinstance(call.parent, pr.StatementElement):
            call = call.parent
        arr.parent.execution = None
        return call if isinstance(call, pr.Call) else None, index, False
    return None, 0, False
