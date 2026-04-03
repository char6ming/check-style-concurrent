# prepare

install python package
```shell
$ pre-commit install
```

# usage

* normal usage
```shell
$ python3 tools/check_style_concurrent.py
```

* skip *.pb.h and  ext dir, disable failure info ...
```shell
python3 -c "
import os, sys
w = os.walk
os.walk = lambda t, **k: ((dirs.remove('ext') if 'ext' in dirs else 0, (root, dirs, files))[1] for root, dirs, files in w('/home/work/xxx', **k))
script_path = os.path.abspath('tools/check_style_concurrent.py')
__file__ = script_path
with open(script_path) as f:
    src = f.read().replace(
        \"fname.endswith(VALID_EXTS) and 'test' not in fname\",
        \"fname.endswith(VALID_EXTS) and 'test' not in fname and not fname.endswith('.pb.h')\"
    ).replace(
        \"if results['failed']:\", 
        \"if False: \"
    )
exec(src, globals())
"
```

