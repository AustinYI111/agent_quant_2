"""Portable references to a code project and an explicit external work drive."""
from pathlib import Path


class WorkspacePaths:
    def __init__(self, project, work_root):
        if work_root is None:
            raise ValueError('explicit work root required')
        self.project=Path(project).resolve();self.work=Path(work_root).resolve()
        if not self.project.is_dir() or not self.work.is_dir():
            raise ValueError('existing project and work roots required')
        if self.project.is_relative_to(self.work) or self.work.is_relative_to(self.project):
            raise ValueError('separate nonoverlapping project and work roots required')

    def resolve(self, value):
        if not isinstance(value,str) or not value:
            raise ValueError('nonempty portable path required')
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in value or '\\' in value:
            raise ValueError('portable relative path required')
        parts=p.parts
        base=self.work if parts and parts[0]=='@work' else self.project
        relative=Path(*parts[1:]) if base==self.work else p
        if str(relative)=='.':raise ValueError('file or dataset path required')
        result=(base/relative).resolve()
        if not result.is_relative_to(base):
            raise ValueError('resolved workspace path escapes selected root')
        return result

    def name(self, value):
        p=Path(value).resolve()
        if p.is_relative_to(self.work):return '@work/'+p.relative_to(self.work).as_posix()
        if p.is_relative_to(self.project):
            name=p.relative_to(self.project).as_posix()
            if name.split('/')[0]=='@work':raise ValueError('reserved workspace namespace')
            return name
        raise ValueError('path outside explicit workspace roots')
