package triad.core

import org.apache.spark.sql.functions.sum

/** TC-036: every stage a job runs is measured, and a shuffle shows up as bytes, not as zero. */
class StageMetricsSpec extends SparkFixture {

  import spark.implicits._

  test("TC-036: a shuffling job reports its stages, shuffle bytes and task skew") {
    val listener = new StageMetricsListener
    spark.sparkContext.addSparkListener(listener)
    try {
      (1 to 2000).map(i => (i % 7, i.toLong)).toDF("k", "v").groupBy("k").agg(sum("v")).collect()

      // Listener events arrive asynchronously. A job stops Spark to drain them (TriadJob); this
      // suite's session outlives the test, so it waits for the stages instead.
      val deadline = System.currentTimeMillis() + 10000L
      while (listener.stages.size < 2 && System.currentTimeMillis() < deadline) Thread.sleep(50L)

      val stages = listener.stages
      assert(stages.size >= 2, stages)
      assert(stages.map(_.shuffleWriteBytes).sum > 0L, "the map side of the groupBy wrote no shuffle")
      assert(stages.map(_.shuffleReadBytes).sum > 0L, "the reduce side read no shuffle")
      assert(stages.forall(s => s.numTasks > 0 && s.taskSkew >= 1.0 && !s.failed))
      assert(listener.toJson.startsWith("""[{"stage_id":"""))
    } finally spark.sparkContext.removeSparkListener(listener)
  }
}
