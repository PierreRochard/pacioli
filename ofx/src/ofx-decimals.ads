--  OFX amounts: an optional sign, digits, and an optional '.' or ',' decimal
--  point. No exponent, grouping, or special values. The digits are kept as
--  written, so Pacioli receives the exact value and its scale.

package Ofx.Decimals with SPARK_Mode is

   Max_Integer_Digits  : constant := 18;
   Max_Fraction_Digits : constant := 8;

   type Decimal is record
      Negative : Boolean := False;   --  False for every zero
      Whole    : Span := No_Span;    --  integer digits without leading zeros
      Fraction : Span := No_Span;    --  fractional digits as written
   end record;

   function Digits_Only (Text : String; S : Span) return Boolean is
     (for all I in S.First .. S.Last => Text (I) in '0' .. '9')
   with Pre => Within (S, Text);

   function Well_Formed (Text : String; D : Decimal) return Boolean is
     (Within (D.Whole, Text) and then Within (D.Fraction, Text)
      and then Length (D.Whole) <= Max_Integer_Digits
      and then Length (D.Fraction) <= Max_Fraction_Digits
      and then Digits_Only (Text, D.Whole) and then Digits_Only (Text, D.Fraction)
      and then (Is_Empty (D.Whole) or else Text (D.Whole.First) /= '0'));

   procedure Parse (Text : String; S : Span; Result : out Decimal; Ok : out Boolean)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input and then Within (S, Text),
     Post => (if Ok then Well_Formed (Text, Result));

end Ofx.Decimals;
