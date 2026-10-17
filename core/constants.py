# core/constants.py - 框架级常量（单一事实来源）
#
# 用例分级标记白名单：必须与 pytest.ini 的 markers 注册项保持一致。
# 新增标记时改这两处即可（testcases/test_core_unit.py::TestMarkers 会校验两者同步）。
# 用例侧的白名单校验统一读这里，避免出现"三处各写一份、加标记漏改"的问题。
#
# auto: plugins/casegen/gen_cases.py 自动生成的用例标记，便于通过 -k "not auto" 排除自动用例
#      仅扩展集合，不改变现有语义；手写用例无需手动加该标记
KNOWN_MARKERS = ("smoke", "regression", "p0", "p1", "auto")
