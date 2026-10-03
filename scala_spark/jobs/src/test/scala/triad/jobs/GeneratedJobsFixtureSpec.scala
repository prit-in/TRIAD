package triad.jobs

import org.apache.spark.sql.{DataFrame, Row}
import org.apache.spark.sql.functions.col
import triad.core.{ChurnFixtures, CleaningRules, Inputs, JobConfig, SparkFixture, TriadJob}

import java.io.File
import java.net.URLDecoder
import java.nio.file.Files
import scala.jdk.CollectionConverters._

/**
 * Validation gate 5 (PRD 21.1): the **generated** job, on small hand-checked data, with a local
 * SparkSession.
 *
 * The specs in `core` hold the building blocks a slot composes - `ChurnTransforms`,
 * `CleaningRules` - to hand-computed values. They cannot hold the composition: a slot that
 * calls the right functions in the wrong order, with the wrong window, or on the wrong input
 * passes every one of them. This suite loads the generated object itself and holds its output
 * to the same numbers, through `transform` exactly as `TriadJob.main` calls it.
 *
 * Which objects: `TRIAD_GENERATED_OBJECTS` (comma-separated, fully qualified) when the pipeline
 * runs this as gate 5 for one artifact; otherwise every object compiled into
 * `triad.jobs.generated`, so `sbt jobs/test` checks whatever is there.
 */
class GeneratedJobsFixtureSpec extends SparkFixture with ChurnFixtures {

  import spark.implicits._

  private val Package = "triad.jobs.generated"
  private val GeneratedName = """^(CleanJob|FeatureJob|LabelJob)_[0-9a-f]+\$\.class$""".r

  private val objects: Seq[String] =
    sys.env.get("TRIAD_GENERATED_OBJECTS").map(_.trim).filter(_.nonEmpty) match {
      case Some(list) => list.split(",").map(_.trim).filter(_.nonEmpty).toSeq
      case None       => discover()
    }

  /** Every generated object on the test classpath. Directories only: that is where sbt puts them. */
  private def discover(): Seq[String] = {
    val path = Package.replace('.', '/')
    getClass.getClassLoader.getResources(path).asScala.toSeq
      .filter(_.getProtocol == "file")
      .flatMap { url =>
        val dir = new File(URLDecoder.decode(url.getPath, "UTF-8"))
        Option(dir.listFiles()).toSeq.flatten.map(_.getName)
      }
      .collect { case name @ GeneratedName(_) => s"$Package.${name.stripSuffix("$.class")}" }
      .distinct
      .sorted
  }

  private def load(fqn: String): TriadJob =
    Class.forName(fqn + "$").getField("MODULE$").get(null).asInstanceOf[TriadJob]

  private def config(extra: String*): JobConfig = {
    val tmp = Files.createTempDirectory("gate5").toFile.getAbsolutePath
    JobConfig.parse(
      Array(
        "--run-key=gate5-fixture",
        s"--output=file://$tmp/out",
        s"--staging=file://$tmp/staging",
        s"--quarantine=file://$tmp/quarantine",
        "--param=max_window_days=365",
        s"--param=first_cutoff=$Cutoff",
        s"--param=last_cutoff=$Cutoff",
        s"--param=data_end=$DataEnd",
        "--param=horizon_days=90"
      ) ++ extra
    )
  }

  private def byCustomer(df: DataFrame): Map[String, Row] =
    df.collect().map(r => r.getAs[String]("customer_id") -> r).toMap

  private def featureInputs(withPostCutoff: Boolean): Inputs =
    Inputs.of(
      "transactions" -> cleanLines(withPostCutoff),
      "products" -> products,
      "returns" -> returnsTable(withPostCutoff),
      "web_sessions" -> sessions(withPostCutoff),
      "support_tickets" -> tickets(withPostCutoff)
    )

  if (objects.isEmpty) {
    ignore("gate 5: no generated jobs are compiled, so there is nothing to check") {}
  }

  objects.foreach { fqn =>
    val simple = fqn.split('.').last

    if (simple.startsWith("FeatureJob_")) {
      test(s"gate 5: $simple matches the hand-checked feature catalogue at T") {
        val job = load(fqn)
        val rows = byCustomer(job.transform(spark, featureInputs(withPostCutoff = true), config()))
        assert(rows.keySet == Set("C1", "C2", "C3"))
        assertFeatures(rows("C1"), Expected.c1, s"$simple C1")
        assertFeatures(rows("C2"), Expected.c2, s"$simple C2")
        assertFeatures(rows("C3"), Expected.c3, s"$simple C3")
        // Every group's source was supplied, so every group must say so.
        val unavailable = job.jobMetrics.collect { case (k, "unavailable") => k }
        assert(unavailable.isEmpty, s"groups reported unavailable with their source present: $unavailable")
      }

      test(s"gate 5: $simple - events on or after T change no feature value (TC-026)") {
        val job = load(fqn)
        val before = job.transform(spark, featureInputs(withPostCutoff = false), config())
          .orderBy(col("customer_id")).collect()
        val after = job.transform(spark, featureInputs(withPostCutoff = true), config())
          .orderBy(col("customer_id")).collect()
        assert(before.length == after.length)
        before.zip(after).foreach { case (b, a) =>
          b.schema.fieldNames.foreach { name =>
            assert(b.getAs[Any](name) == a.getAs[Any](name),
                   s"$simple ${b.getAs[String]("customer_id")}.$name leaked a post-cutoff event")
          }
        }
      }
    } else if (simple.startsWith("LabelJob_")) {
      test(s"gate 5: $simple labels match the hand-checked fixture") {
        val labels = load(fqn)
          .transform(spark, Inputs.of("transactions" -> cleanLines()), config())
          .collect()
          .map(r => r.getAs[String]("customer_id") -> r.getAs[Int]("churned_90d"))
          .toMap
        assert(labels == Expected.labels, s"$simple: $labels")
      }
    } else if (simple.startsWith("CleanJob_")) {
      test(s"gate 5: $simple quarantines, de-duplicates and turns cancellations into returns") {
        val raw = Seq[(String, Int, Option[String], Int, Double)](
          ("O1", 1, Some("C1"), 2, 100.0),
          ("O2", 1, None, 1, 50.0),        // NO_CUSTOMER_KEY
          ("O3", 1, Some("C2"), 1, -10.0), // NON_POSITIVE_PRICE
          ("O4", 1, Some("C3"), 0, 20.0),  // ZERO_QUANTITY
          ("O1", 1, Some("C1"), 2, 100.0), // exact duplicate of O1
          ("O5", 1, Some("C4"), 3, 30.0),
          ("CO6", 1, Some("C4"), -1, 30.0) // a cancellation: kept, as a return
        ).toDF("order_id", "line_id", "customer_id", "quantity", "price")

        val cfg = config("--param=dedup_keys=order_id,line_id")
        val job = load(fqn)
        val out = job.transform(spark, Inputs.of("source" -> raw), cfg)

        val metrics = job.jobMetrics
        assert(metrics("input_rows") == "7")
        assert(metrics("output_rows") == "3")
        assert(metrics("quarantined_rows") == "3")
        assert(metrics("removed_duplicates") == "1")
        assert(metrics("row_accounting_balanced") == "true")

        val cancellation = out.where(col("order_id") === "CO6").collect()
        assert(cancellation.length == 1, "a cancellation must survive cleaning as a return")
        assert(cancellation.head.getAs[Boolean]("is_return"))
        assert(cancellation.head.getAs[Int]("quantity") == 1, "its quantity is made positive")

        val reasons = spark.read.parquet(cfg.quarantinePath.get)
          .select(col(CleaningRules.ReasonColumn)).as[String].collect().toSet
        assert(reasons == Set("NO_CUSTOMER_KEY", "NON_POSITIVE_PRICE", "ZERO_QUANTITY"))
      }
    } else {
      test(s"gate 5: $simple has no fixture") {
        fail(s"$fqn is not a recognised template; gate 5 cannot vouch for it")
      }
    }
  }
}
