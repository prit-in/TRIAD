// TRIAD Scala build (PRD CR-02, FR-041). Spark is `provided`: the JAR stays thin and the
// cluster supplies Spark. Generated code may never edit this file (gate 3, PRD 21.1).

ThisBuild / organization := "org.triad"
ThisBuild / version := "0.1.0"
// Must match the Scala version of the Spark build exactly (CR-02). Verified against
// $SPARK_HOME/jars/scala-library-2.13.17.jar in triad-base:0.1.
ThisBuild / scalaVersion := "2.13.17"

val sparkVersion = "4.1.3"

lazy val commonSettings = Seq(
  scalacOptions ++= Seq(
    "-deprecation",
    "-feature",
    "-unchecked",
    "-encoding", "UTF-8",
    "-Xlint:_",
    "-Ywarn-dead-code",
    "-Ywarn-unused:imports,privates,locals"
  ),
  libraryDependencies ++= Seq(
    "org.apache.spark" %% "spark-core" % sparkVersion % Provided,
    "org.apache.spark" %% "spark-sql"  % sparkVersion % Provided,
    "org.scalatest"    %% "scalatest"  % "3.2.19"     % Test
  ),
  // Spark needs these open for its use of sun.nio / reflection on JDK 17.
  Test / javaOptions ++= Seq(
    "--add-opens=java.base/java.lang=ALL-UNNAMED",
    "--add-opens=java.base/java.lang.invoke=ALL-UNNAMED",
    "--add-opens=java.base/java.io=ALL-UNNAMED",
    "--add-opens=java.base/java.net=ALL-UNNAMED",
    "--add-opens=java.base/java.nio=ALL-UNNAMED",
    "--add-opens=java.base/java.util=ALL-UNNAMED",
    "--add-opens=java.base/java.util.concurrent=ALL-UNNAMED",
    "--add-opens=java.base/sun.nio.ch=ALL-UNNAMED",
    "--add-opens=java.base/sun.security.action=ALL-UNNAMED",
    "-Dspark.ui.enabled=false",
    "-Xmx1500m"
  ),
  Test / fork := true,
  Test / parallelExecution := false
)

lazy val core = (project in file("core"))
  .settings(commonSettings)
  .settings(name := "triad-core")

lazy val jobs = (project in file("jobs"))
  .dependsOn(core % "compile->compile;test->test")
  .settings(commonSettings)
  .settings(
    name := "triad-jobs",
    // Warnings are errors in the generated module only (PRD 21.1 gate 4).
    Compile / scalacOptions ++= Seq("-Wconf:src=.*generated.*:error")
  )

lazy val root = (project in file("."))
  .aggregate(core, jobs)
  .settings(name := "triad-spark", publish / skip := true)
