--  Pacioli's OFX component: a bounded, side-effect-free reader of bank and
--  credit-card statement responses, and a writer of statement requests.
--  Persistence and acceptance policy belong to Pacioli; see ../README.md.

package Ofx with SPARK_Mode, Pure is

   --  Pacioli's MAX_OFX_BYTES. Longer input is rejected before parsing.
   Max_Input : constant := 10 * 1024 * 1024;

   --  A run of input characters, empty when Last < First. Values keep their
   --  place in the input instead of being copied.
   type Span is record
      First : Positive := 1;
      Last  : Natural  := 0;
   end record;

   No_Span : constant Span := (First => 1, Last => 0);

   function Is_Empty (S : Span) return Boolean is (S.Last < S.First);

   function Length (S : Span) return Natural is
     (if S.Last < S.First then 0 else S.Last - S.First + 1);

   function Within (S : Span; Text : String) return Boolean is
     (Is_Empty (S) or else (S.First >= Text'First and then S.Last <= Text'Last));

   function Matches (Text : String; S : Span; Literal : String) return Boolean is
     (Length (S) = Literal'Length
      and then (Length (S) = 0 or else Text (S.First .. S.Last) = Literal))
   with Pre => Within (S, Text);

   function Same (Text : String; Left, Right : Span) return Boolean is
     (Length (Left) = Length (Right)
      and then (Length (Left) = 0
                or else Text (Left.First .. Left.Last) = Text (Right.First .. Right.Last)))
   with Pre => Within (Left, Text) and then Within (Right, Text);

   --  How the bytes of character data map to Unicode, from the OFX header.
   type Charset is (Us_Ascii, Windows_1252, Latin_1, Utf_8);

end Ofx;
