"""Employee-only sandbox mount changes without the pending ownership rewrite."""

import ast

from gateway_runtime_delta import definition, replace_once


def add_method(source, candidate, owner, method, before):
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner]
    if len(classes) != 1 or any(getattr(node, "name", None) == method for node in classes[0].body):
        raise ValueError("New employee method conflicts with production: " + owner + "." + method)
    anchor = definition(source, owner + "." + before)
    return replace_once(source, owner + "." + before, anchor, definition(candidate, owner + "." + method) + "\n" + anchor)


def aio_provider(source, candidate):
    owner = "AioSandboxProvider"
    method = owner + "._get_skills_mounts"
    source = replace_once(source, method, definition(source, method), definition(candidate, method))
    source = replace_once(source, owner + "._get_extra_mounts", "self._get_skills_mounts(user_id=user_id)", 'self._get_skills_mounts(user_id=user_id, **({"thread_id": thread_id} if thread_id else {}))')
    source = add_method(source, candidate, owner, "validate_employee_skills", "acquire")
    # Keep production's thread/file/execution locks and warm-pool destruction.
    # Validate even a cached acquisition, inside the original thread lock.
    source = replace_once(source, owner + ".acquire",
                          "                return self._acquire_internal(thread_id, user_id=effective_user_id)\n",
                          "                sandbox_id = self._acquire_internal(thread_id, user_id=effective_user_id)\n"
                          "                self.validate_employee_skills(sandbox_id, thread_id, user_id=effective_user_id)\n"
                          "                return sandbox_id\n")
    return replace_once(source, owner + ".acquire_async",
                        "                return await self._acquire_internal_async(thread_id, user_id=effective_user_id)\n",
                        "                sandbox_id = await self._acquire_internal_async(thread_id, user_id=effective_user_id)\n"
                        "                await asyncio.to_thread(self.validate_employee_skills, sandbox_id, thread_id, user_id=effective_user_id)\n"
                        "                return sandbox_id\n")


def local_backend(source, candidate):
    return add_method(source, candidate, "LocalContainerBackend", "validate_employee_skills", "create")


def sandbox_tools(source, candidate):
    return replace_once(source, "read_file_tool",
                        "_is_disabled_skill_path(path, user_id=resolve_runtime_user_id(runtime))",
                        "_is_disabled_skill_path(path, user_id=resolve_runtime_user_id(runtime), thread_id=_runtime_thread_id(runtime))")


ADAPTERS = {"aio_provider": aio_provider, "local_backend": local_backend, "sandbox_tools": sandbox_tools}
