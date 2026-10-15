// Jenkins 声明式流水线样例（Windows 节点使用 bat；Linux 节点将 bat 换成 sh 即可）
//
// 首次使用：
// 1. Jenkins 安装插件：HTML Publisher（发布 report.html）
// 2. 流水线定义选择「Pipeline script from SCM」，指向本文件
pipeline {
    agent any

    environment {
        TEST_ENV       = 'dev'          // 运行环境：dev / staging / prod
    }

    options {
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '15'))
    }

    stages {
        stage('安装依赖') {
            steps {
                bat 'python -m pip install -r requirements.txt'
            }
        }

        stage('静态检查') {
            // 捕获语法错误 / 未定义名称等"代码必然有问题"的问题（规则见 ruff.toml）
            steps {
                bat 'python -m ruff check core api testcases conftest.py'
            }
        }

        stage('用例收集检查') {
            // 不登录、不发请求，仅校验 YAML 用例能否被正常收集
            // （用例模型校验、接口 key 校验、断言强度校验都在这一阶段暴露）
            steps {
                bat "python -m pytest --collect-only --env=%TEST_ENV%"
            }
        }

        stage('冒烟测试') {
            // 每次构建都跑：只执行 YAML 中带 markers: [smoke] 的核心链路用例
            // --reruns 1 仅用于屏蔽偶发网络抖动，不代表功能问题（重跑仍失败即为真回归）
            steps {
                bat "python -m pytest -m smoke --env=%TEST_ENV% --reruns 1 --reruns-delay 3"
            }
        }

        stage('全量回归') {
            // 仅 main 分支触发，或在 Jenkins 中按需手动构建
            // 注意：这里不带 -m 过滤，执行 test_data/ 下全部用例（含未打标记的生成用例）；
            //       生成用例默认不带 markers，用 -m regression 会一条都匹配不到（pytest 退出码 5）
            when {
                branch 'main'
            }
            steps {
                bat "python -m pytest --env=%TEST_ENV% --cov --cov-report=term-missing"
            }
        }
    }

    post {
        always {
            // 发布 pytest-html 报告（需 HTML Publisher 插件）
            publishHTML(target: [
                allowMissing: true,
                alwaysLinkToLastBuild: true,
                keepAll: true,
                reportDir: 'output/reports',
                reportFiles: 'report.html',
                reportName: '接口测试报告'
            ])
        }
        cleanup {
            cleanWs()
        }
    }
}
