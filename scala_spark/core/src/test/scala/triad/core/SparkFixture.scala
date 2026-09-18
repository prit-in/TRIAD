package triad.core

import org.apache.spark.sql.SparkSession
import org.scalatest.BeforeAndAfterAll
import org.scalatest.funsuite.AnyFunSuite

/**
 * One local SparkSession per suite, configured exactly like production: ANSI on, UTC,
 * microsecond timestamps. Fixture data is small and hand-checked, so the expected values in
 * these tests are computed by hand, not by the code under test.
 *
 * `spark` is a stable `lazy val` so suites can `import spark.implicits._`.
 */
trait SparkFixture extends AnyFunSuite with BeforeAndAfterAll {

  @transient protected lazy val spark: SparkSession = {
    val s = SparkSession
      .builder()
      .appName("triad-fixtures")
      .master("local[2]")
      .config("spark.sql.ansi.enabled", "true")
      .config("spark.sql.session.timeZone", "UTC")
      .config("spark.sql.parquet.outputTimestampType", "TIMESTAMP_MICROS")
      .config("spark.sql.shuffle.partitions", "2")
      .config("spark.ui.enabled", "false")
      .config("spark.driver.host", "localhost")
      .getOrCreate()
    s.sparkContext.setLogLevel("ERROR")
    s
  }

  override def afterAll(): Unit = {
    try spark.stop()
    finally super.afterAll()
  }
}
