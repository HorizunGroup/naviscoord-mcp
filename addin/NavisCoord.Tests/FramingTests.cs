using System;
using System.Linq;
using Box3 = NavisCoord.ClashFraming.Box3;
using Vec3 = NavisCoord.ClashFraming.Vec3;

namespace NavisCoord.Tests
{
    /// <summary>
    /// The camera arithmetic, asserted without Navisworks.
    /// </summary>
    /// <remarks>
    /// Every case here is a picture that came back wrong from a real model,
    /// reduced to the numbers that produced it. That is the point of keeping
    /// <see cref="ClashFraming"/> free of Autodesk types: a bad frame does not
    /// throw, it renders, so the only place the failure is visible is in the
    /// coordinates — and those can be checked on a runner with no licence.
    ///
    /// The recurring assertion is the projection one. "The camera is 4.2 m
    /// away" is not a statement about whether the reader can see the clash;
    /// "both boxes land inside [-1, 1] on screen" is.
    /// </remarks>
    internal static class FramingTests
    {
        private static Action<string> _section;
        private static Action<object, object, string> _eq;
        private static Action<bool, string> _check;

        public static void Run(
            Action<string> section,
            Action<object, object, string> eq,
            Action<bool, string> check)
        {
            _section = section;
            _eq = eq;
            _check = check;

            BoxArithmetic();
            TinyClash();
            HugeElements();
            BothSidesVisible();
            StackedElements();
            PlanMode();
            PerspectiveContainment();
            Clamps();
            Degenerate();
            ModeLadder();
            ModelNaming();
        }

        /// <summary>
        /// Decoding ACC's model names, which used to live on a ribbon button.
        /// </summary>
        /// <remarks>
        /// It travels with the framing tests because removing the buttons is
        /// what surfaced it: three callers of a UI class, none of them UI. The
        /// rule it protects is that a profile written "-SEÑ-" still matches a
        /// tree that says "-SE%C3%91-", or the discipline goes unassigned in
        /// silence.
        /// </remarks>
        private static void ModelNaming()
        {
            _section("nombres de modelo");

            _eq("PROY-SEÑ-01.rvt", ModelNames.Decode("PROY-SE%C3%91-01.rvt"),
                "los %XX de ACC se decodifican");
            _eq("PROY-EST-01.rvt", ModelNames.Decode("PROY-EST-01.rvt"),
                "un nombre sin codificar no se toca");
            _eq("", ModelNames.Decode(null), "null no revienta");
            _eq("", ModelNames.Decode(""), "vacío se queda vacío");
            _eq("100% acero", ModelNames.Decode("100% acero"),
                "un porcentaje suelto no es un escape: vuelve tal cual en vez de lanzar");
        }

        // ------------------------------------------------------------ boxes

        private static void BoxArithmetic()
        {
            _section("marcos: cajas");

            var box = new Box3(new Vec3(1, 2, 3), new Vec3(4, 6, 9));
            _eq(3.0, box.Size.X, "ancho de la caja");
            _eq(2.5, box.Center.X, "centro en X");
            _eq(6.0, box.LongestSide, "lado mayor");

            // Constructed from swapped corners: a bounding box from a source
            // that reports max before min must not come out inside out.
            var flipped = new Box3(new Vec3(4, 6, 9), new Vec3(1, 2, 3));
            _eq(box.Center.X, flipped.Center.X, "min y max invertidos se normalizan");
            _eq(3.0, flipped.Size.X, "el tamaño no sale negativo");

            _check(Box3.Empty.IsEmpty, "la caja vacía se reconoce");
            _check(!box.IsEmpty, "una caja con volumen no es vacía");
            _check(Box3.Empty.Union(box).Center.X == box.Center.X, "unir con vacía devuelve la otra");
            _check(box.Union(Box3.Empty).Center.X == box.Center.X, "unir vacía con otra devuelve la otra");

            var far = new Box3(new Vec3(100, 100, 100), new Vec3(101, 101, 101));
            _check(box.Intersection(far).IsEmpty, "cajas separadas no se cortan");
            _check(!box.Intersection(new Box3(new Vec3(0, 0, 0), new Vec3(2, 3, 4))).IsEmpty,
                "cajas solapadas sí se cortan");

            var grown = box.Expanded(1.0);
            _eq(5.0, grown.Size.X, "expandir suma en las dos caras");

            var floored = new Box3(new Vec3(0, 0, 0), new Vec3(0, 0, 0)).AtLeast(2.0);
            _eq(2.0, floored.Size.X, "una caja degenerada crece hasta el mínimo");
            _eq(0.0, floored.Center.X, "y lo hace alrededor de su centro");

            _eq(8, box.Corners().Count(), "una caja tiene ocho esquinas");
            _eq(0, Box3.Empty.Corners().Count(), "una caja vacía no tiene esquinas");
        }

        // ------------------------------------------------------- the two bugs

        private static void TinyClash()
        {
            _section("marcos: cruce milimétrico");

            // A duct clipping a beam by 8 mm. The old rule was
            // distance = span * 2.2, which put the camera 18 mm from the
            // target — inside the duct — and rendered flat grey.
            var clash = new Box3(new Vec3(0, 0, 0), new Vec3(0.008, 0.6, 0.008));
            var beam = new Box3(new Vec3(-0.2, -8, -0.3), new Vec3(0.2, 8, 0.3));
            var duct = new Box3(new Vec3(-30, -0.3, -0.3), new Vec3(30, 0.3, 0.3));

            var frame = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = beam, BoxB = duct, Mode = ClashFraming.ModeCloseup
            });

            _check(frame.Distance >= 1.5, $"la cámara no se mete dentro (distancia {frame.Distance:0.###} m)");
            _check(frame.ExtentHeight >= 0.8, "el encuadre no baja del mínimo legible");

            var oldRule = Math.Max(clash.LongestSide, 1e-6) * 2.2;
            _check(oldRule < 1.5, $"la regla anterior daba {oldRule:0.###} m: el fallo existía");

            AssertFramesBoth(frame, beam, duct, clash, "cruce milimétrico");
        }

        private static void HugeElements()
        {
            _section("marcos: elementos enormes");

            // A 60 m duct against a 16 m beam. Fitting their whole boxes is
            // the aerial photograph: the frame would be tens of metres wide
            // and the clash a couple of pixels.
            var clash = new Box3(new Vec3(-0.1, -0.1, -0.05), new Vec3(0.1, 0.1, 0.05));
            var beam = new Box3(new Vec3(-0.2, -8, -0.3), new Vec3(0.2, 8, 0.3));
            var duct = new Box3(new Vec3(-30, -0.3, -0.3), new Vec3(30, 0.3, 0.3));

            var frame = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = beam, BoxB = duct, Mode = ClashFraming.ModeCloseup
            });

            var wholeThing = beam.Union(duct);
            _check(wholeThing.LongestSide > 55, "las cajas completas abarcan más de 55 m");
            _check(frame.ExtentWidth < 8.0,
                $"el encuadre se queda en la vecindad del cruce ({frame.ExtentWidth:0.##} m de ancho)");
            _check(frame.FitBox.LongestSide < wholeThing.LongestSide / 5.0,
                "la caja encuadrada es una fracción de la geometría completa");

            AssertFramesBoth(frame, beam, duct, clash, "elementos enormes");

            // Context deliberately pulls back, and just as deliberately does
            // not pull back to the whole building.
            var context = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = beam, BoxB = duct, Mode = ClashFraming.ModeContext
            });
            _check(context.ExtentWidth > frame.ExtentWidth, "contexto encuadra más ancho que primer plano");
            _check(context.ExtentWidth < wholeThing.LongestSide,
                "…y aun así menos que la geometría entera");
        }

        // --------------------------------------------------------- occlusion

        private static void BothSidesVisible()
        {
            _section("marcos: los dos lados en cuadro");

            var clash = new Box3(new Vec3(-0.15, -0.15, -0.1), new Vec3(0.15, 0.15, 0.1));
            var a = new Box3(new Vec3(-0.25, -6, -0.35), new Vec3(0.25, 6, 0.35));
            var b = new Box3(new Vec3(-10, -0.3, -0.25), new Vec3(10, 0.3, 0.25));

            foreach (var mode in new[] { ClashFraming.ModeCloseup, ClashFraming.ModeContext, ClashFraming.ModePlan })
            {
                var frame = ClashFraming.Frame(new ClashFraming.Request
                {
                    ClashBox = clash, BoxA = a, BoxB = b, Mode = mode
                });
                AssertFramesBoth(frame, a, b, clash, "modo " + mode);
            }
        }

        private static void StackedElements()
        {
            _section("marcos: elementos alineados con la vista");

            // Two elements whose centres are separated along the direction a
            // fixed three-quarter camera would look from. With a fixed
            // direction the near one hides the far one; the direction has to
            // turn perpendicular to the separation.
            var separation = new Vec3(-1.0, 1.0, -0.6).Normalized(new Vec3(0, 1, 0));
            var centreA = new Vec3(0, 0, 0);
            var centreB = centreA.Plus(separation.Scaled(1.2));

            var a = Around(centreA, 0.3);
            var b = Around(centreB, 0.3);
            var clash = Around(centreA.Plus(separation.Scaled(0.6)), 0.1);

            var frame = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = a, BoxB = b, Mode = ClashFraming.ModeCloseup
            });

            var alignment = Math.Abs(frame.Direction.Dot(separation));
            _check(alignment < 0.5,
                $"la vista gira fuera del eje que une los dos elementos (alineación {alignment:0.###})");

            var ra = ClashFraming.Project(frame, a);
            var rb = ClashFraming.Project(frame, b);
            var gap = Math.Max(
                Math.Abs(((ra.MinX + ra.MaxX) / 2) - ((rb.MinX + rb.MaxX) / 2)),
                Math.Abs(((ra.MinY + ra.MaxY) / 2) - ((rb.MinY + rb.MaxY) / 2)));
            _check(gap > 0.15, $"los dos lados quedan separados en pantalla (separación {gap:0.###})");

            AssertFramesBoth(frame, a, b, clash, "elementos alineados");
        }

        // -------------------------------------------------------------- plan

        private static void PlanMode()
        {
            _section("marcos: planta");

            var clash = new Box3(new Vec3(-0.2, -0.2, -0.1), new Vec3(0.2, 0.2, 0.1));
            var a = new Box3(new Vec3(-3, -0.3, -0.3), new Vec3(3, 0.3, 0.3));
            var b = new Box3(new Vec3(-0.3, -3, -0.3), new Vec3(0.3, 3, 0.3));

            var frame = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = a, BoxB = b, Mode = ClashFraming.ModePlan
            });

            // Perspective, not orthographic. An orthographic view volume is a
            // box, so everything above the clash is drawn whatever the camera
            // height, and on a plenum the slab and its services wash both
            // elements out — measured at 0.0% of the frame for both sides on a
            // live federation. In perspective what is behind the lens is not
            // drawn, so a camera just above the clash leaves the slab out.
            _check(!frame.Orthographic, "planta usa perspectiva, no ortográfica");
            _check(frame.Direction.Z < -0.99, "planta mira hacia abajo");
            _check(frame.Position.Z > frame.Target.Z, "la cámara queda por encima del objetivo");

            // And it stands as close as the geometry allows rather than at
            // min_distance, because in a top-down view the distance decides
            // what gets between the lens and the clash.
            var far = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = a, BoxB = b,
                Mode = ClashFraming.ModePlan, MinDistance = 25.0
            });
            _check(far.Distance < 25.0,
                $"planta ignora min_distance para no meter el forjado en medio ({far.Distance:0.##} m)");
            AssertFramesBoth(far, a, b, clash, "planta con min_distance alto");

            var oblique = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = a, BoxB = b,
                Mode = ClashFraming.ModeCloseup, MinDistance = 25.0
            });
            _check(oblique.Distance >= 25.0,
                "…y los demás modos siguen respetándolo");
            _check(Math.Abs(frame.Up.Z) < 0.01, "el vector arriba de la planta es horizontal");
            AssertFramesBoth(frame, a, b, clash, "planta");

            // A Y-up document must not come out looking sideways.
            var yUp = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = a, BoxB = b,
                Mode = ClashFraming.ModePlan, WorldUp = new Vec3(0, 1, 0)
            });
            _check(yUp.Direction.Y < -0.99, "en un documento Y arriba, la planta mira según -Y");
            AssertFramesBoth(yUp, a, b, clash, "planta Y arriba");
        }

        // ------------------------------------------------------------ límites

        private static void Clamps()
        {
            _section("marcos: límites de distancia");

            var clash = new Box3(new Vec3(-0.5, -0.5, -0.5), new Vec3(0.5, 0.5, 0.5));
            var a = new Box3(new Vec3(-2, -2, -2), new Vec3(2, 2, 2));
            var b = new Box3(new Vec3(-1, -1, -1), new Vec3(1, 1, 1));

            // Derived from the unclamped answer rather than hard-coded, so
            // the case stays a genuine cap when the neighbourhood constants
            // move. A literal 3.0 quietly stopped binding when they did, and
            // the test then asserted nothing.
            var natural = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = a, BoxB = b
            }).Distance;
            var limit = natural * 0.8;

            var capped = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = a, BoxB = b, MaxDistance = limit
            });
            _check(capped.Distance <= limit + 1e-9, "max_distance acota la distancia");
            _check(capped.Notes.Contains("distance_capped_at_maximum"), "y queda registrado");
            _check(capped.Distance < natural, "y la cámara se acerca de verdad");

            // Asked to stand closer than the geometry is deep. Honouring that
            // literally puts the lens inside the clash and renders grey, so
            // the floor wins and the answer says which option was overruled.
            var impossible = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = a, BoxB = b, MaxDistance = 0.2
            });
            _check(impossible.Notes.Contains("max_distance_below_geometry_floor"),
                "un max_distance imposible se reporta en vez de obedecerse");
            _check(impossible.Distance > 0.2, "y la cámara se queda fuera de la geometría");
            AssertFramesBoth(impossible, a, b, clash, "max_distance imposible");

            // The whole point of separating extents from distance: capping the
            // distance must not change what is in the picture.
            AssertFramesBoth(capped, a, b, clash, "distancia acotada");

            var raised = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = a, BoxB = b, MinDistance = 25.0
            });
            _check(raised.Distance >= 25.0, "min_distance eleva la distancia");
            _check(raised.Notes.Contains("distance_raised_to_minimum"), "y queda registrado");
            AssertFramesBoth(raised, a, b, clash, "distancia elevada");

            // A margin widens the frame; it does not move the aim.
            var tight = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = a, BoxB = b, MarginPercent = 0.0
            });
            var loose = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = a, BoxB = b, MarginPercent = 100.0
            });
            _check(loose.ExtentWidth > tight.ExtentWidth * 1.5, "el margen ensancha el encuadre");
            _eq(tight.Target.X, loose.Target.X, "el objetivo no se mueve con el margen");

            // The camera aims at the clash, not at the middle of the fitted
            // box: an off-centre element must not drag the aim off the clash.
            var lopsided = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash,
                BoxA = new Box3(new Vec3(0, 0, 0), new Vec3(6, 0.4, 0.4)),
                BoxB = b
            });
            _eq(0.0, lopsided.Target.X, "el objetivo sigue siendo el centro del cruce");
        }

        private static void Degenerate()
        {
            _section("marcos: datos degenerados");

            var clash = new Box3(new Vec3(0, 0, 0), new Vec3(0.1, 0.1, 0.1));

            // One side without bounds at all: composite geometry that reports
            // nothing. It must still produce a usable frame and say so.
            var missing = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash, BoxA = Box3.Empty, BoxB = new Box3(new Vec3(-1, -1, -1), new Vec3(1, 1, 1))
            });
            _check(missing.Notes.Contains("side_a_without_bounds"), "un lado sin caja se reporta");
            _check(missing.ExtentWidth > 0, "y aun así se calcula un encuadre");
            _check(ClashFraming.Project(missing, clash).Contained, "el cruce sigue en cuadro");

            // A side whose box is somewhere else entirely — a composite parent
            // half a building away. Reported, and not allowed to drag the
            // frame across the model.
            var elsewhere = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash,
                BoxA = new Box3(new Vec3(400, 400, 400), new Vec3(401, 401, 401)),
                BoxB = new Box3(new Vec3(-1, -1, -1), new Vec3(1, 1, 1))
            });
            _check(elsewhere.Notes.Contains("side_a_outside_neighbourhood"),
                "un lado fuera de la vecindad se reporta");
            _check(elsewhere.ExtentWidth < 30, "y no arrastra el encuadre hasta él");
            _check(!ClashFraming.Project(elsewhere, new Box3(new Vec3(400, 400, 400), new Vec3(401, 401, 401))).InFrame,
                "ese lado queda declarado fuera de cuadro en vez de fingirse visible");

            // A completely empty clash box: the caller should never send one,
            // and it must not produce NaN if it does.
            var empty = ClashFraming.Frame(new ClashFraming.Request { ClashBox = Box3.Empty });
            _check(!double.IsNaN(empty.Distance) && empty.Distance > 0, "una caja vacía no produce NaN");
            _check(!double.IsNaN(empty.Direction.X), "ni una dirección NaN");

            // Coincident centres: there is no separation axis to be
            // perpendicular to, and the cross product is zero.
            var concentric = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash,
                BoxA = new Box3(new Vec3(-1, -1, -1), new Vec3(1, 1, 1)),
                BoxB = new Box3(new Vec3(-0.5, -0.5, -0.5), new Vec3(0.5, 0.5, 0.5))
            });
            _check(Math.Abs(concentric.Direction.X * concentric.Direction.X
                            + concentric.Direction.Y * concentric.Direction.Y
                            + concentric.Direction.Z * concentric.Direction.Z - 1.0) < 1e-6,
                "geometría concéntrica deja una dirección unitaria");

            // Behind the camera is reported, never projected to a huge number
            // that reads as "far to the right".
            var frame = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = clash,
                BoxA = new Box3(new Vec3(-1, -1, -1), new Vec3(1, 1, 1)),
                BoxB = new Box3(new Vec3(-1, -1, -1), new Vec3(1, 1, 1))
            });
            var behind = frame.Position.Minus(frame.Direction.Scaled(5.0));
            var rect = ClashFraming.Project(frame, Around(behind, 0.5));
            _check(rect.AnyBehindCamera, "lo que queda detrás de la cámara se marca");
            _check(!rect.Contained, "y no se declara contenido");
        }

        private static void ModeLadder()
        {
            _section("marcos: escalera de modos");

            _eq(ClashFraming.ModeContext, ClashFraming.Widen(ClashFraming.ModeCloseup), "primer plano abre a contexto");
            _eq(ClashFraming.ModePlan, ClashFraming.Widen(ClashFraming.ModeContext), "contexto abre a planta");
            _eq(null, ClashFraming.Widen(ClashFraming.ModePlan), "planta es el último escalón");

            _eq(ClashFraming.ModeCloseup, ClashFraming.Normalise(""), "sin modo, primer plano");
            _eq(ClashFraming.ModeCloseup, ClashFraming.Normalise("basura"), "un modo inventado cae en primer plano");
            _eq(ClashFraming.ModePlan, ClashFraming.Normalise("PLANTA"), "se aceptan los nombres en español");
            _eq(ClashFraming.ModeContext, ClashFraming.Normalise("  Contexto "), "y con espacios");
        }

        /// <summary>
        /// A deep box must fit under PERSPECTIVE, not just on the focal plane.
        /// </summary>
        /// <remarks>
        /// The bug this pins cost two wasted renders and a discarded picture
        /// on a live federation. The extents were measured with dot products —
        /// affine, correct at the focal distance — while the render projects
        /// perspectively, so the near face of a deep clash box landed outside
        /// a frame the arithmetic had just declared big enough. Both elements
        /// were plainly visible in the image that got thrown away.
        ///
        /// The case needs DEPTH along the view axis to reproduce: a flat box
        /// has no near face to blow up, which is why every earlier test passed.
        /// </remarks>
        private static void PerspectiveContainment()
        {
            _section("marcos: contención en perspectiva");

            foreach (var depth in new[] { 1.0, 3.0, 8.0 })
            {
                var clash = new Box3(new Vec3(-0.4, -0.4, -depth / 2), new Vec3(0.4, 0.4, depth / 2));
                var a = new Box3(new Vec3(-3, -0.3, -depth / 2), new Vec3(3, 0.3, depth / 2));
                var b = new Box3(new Vec3(-0.3, -3, -depth / 2), new Vec3(0.3, 3, depth / 2));

                var frame = ClashFraming.Frame(new ClashFraming.Request
                {
                    ClashBox = clash, BoxA = a, BoxB = b
                });

                var rect = ClashFraming.Project(frame, clash);
                _check(rect.Contained,
                    $"caja de {depth:0.#} m de fondo: el cruce entero cae dentro del cuadro " +
                    $"(x {rect.MinX:0.##}..{rect.MaxX:0.##}, y {rect.MinY:0.##}..{rect.MaxY:0.##})");
                AssertFramesBoth(frame, a, b, clash, $"fondo {depth:0.#} m");
            }

            // And the correction must not have been "make everything huge":
            // a shallow clash is framed as tightly as it was before.
            var flat = new Box3(new Vec3(-0.4, -0.4, -0.02), new Vec3(0.4, 0.4, 0.02));
            var tight = ClashFraming.Frame(new ClashFraming.Request
            {
                ClashBox = flat,
                BoxA = new Box3(new Vec3(-3, -0.3, -0.1), new Vec3(3, 0.3, 0.1)),
                BoxB = new Box3(new Vec3(-0.3, -3, -0.1), new Vec3(0.3, 3, 0.1)),
            });
            // The correction must not fire when there is nothing to correct:
            // a shallow clash has no near face, so the frame stays where the
            // affine arithmetic put it. Correcting over the FITTED box instead
            // of the clash box widened this same case from 6.6 m to 8.0 m —
            // undoing the tight framing to contain the near end of a duct that
            // is meant to run out of shot.
            _check(tight.ExtentWidth < 7.0,
                $"un cruce plano sigue encuadrado corto ({tight.ExtentWidth:0.##} m)");
        }

        // ----------------------------------------------------------- helpers

        /// <summary>
        /// The assertion this whole file exists for: after framing, both
        /// elements and the interference are inside the picture.
        /// </summary>
        private static void AssertFramesBoth(
            ClashFraming.Result frame, Box3 a, Box3 b, Box3 clash, string what)
        {
            var ra = ClashFraming.Project(frame, a);
            var rb = ClashFraming.Project(frame, b);
            var rc = ClashFraming.Project(frame, clash);

            _check(ra.InFrame, $"{what}: el lado A aparece en cuadro");
            _check(rb.InFrame, $"{what}: el lado B aparece en cuadro");
            _check(rc.Contained, $"{what}: el volumen de interferencia queda completo en cuadro");
            _check(ra.ScreenFraction > 0.002, $"{what}: el lado A ocupa algo más que un píxel");
            _check(rb.ScreenFraction > 0.002, $"{what}: el lado B ocupa algo más que un píxel");
        }

        private static Box3 Around(Vec3 centre, double half)
            => new Box3(
                new Vec3(centre.X - half, centre.Y - half, centre.Z - half),
                new Vec3(centre.X + half, centre.Y + half, centre.Z + half));
    }
}
