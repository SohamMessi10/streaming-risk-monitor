import re

import riskmon


def test_the_package_has_a_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+", riskmon.__version__)
