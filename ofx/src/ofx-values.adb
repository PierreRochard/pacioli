with Ofx.Chars;    use Ofx.Chars;
with Ofx.Dates;
with Ofx.Decimals;
with Ofx.Text;

package body Ofx.Values with SPARK_Mode is

   function Is_Trntype (Text : String; S : Span) return Boolean is
     (Matches (Text, S, "CREDIT") or else Matches (Text, S, "DEBIT")
      or else Matches (Text, S, "INT") or else Matches (Text, S, "DIV")
      or else Matches (Text, S, "FEE") or else Matches (Text, S, "SRVCHG")
      or else Matches (Text, S, "DEP") or else Matches (Text, S, "ATM")
      or else Matches (Text, S, "POS") or else Matches (Text, S, "XFER")
      or else Matches (Text, S, "CHECK") or else Matches (Text, S, "PAYMENT")
      or else Matches (Text, S, "CASH") or else Matches (Text, S, "DIRECTDEP")
      or else Matches (Text, S, "DIRECTDEBIT") or else Matches (Text, S, "REPEATPMT")
      or else Matches (Text, S, "OTHER"))
   with Pre => Within (S, Text);

   function Is_Inv401k (Text : String; S : Span) return Boolean is
     (Matches (Text, S, "PRETAX") or else Matches (Text, S, "AFTERTAX")
      or else Matches (Text, S, "MATCH") or else Matches (Text, S, "PROFITSHARING")
      or else Matches (Text, S, "ROLLOVER") or else Matches (Text, S, "OTHERVEST")
      or else Matches (Text, S, "OTHERNONVEST"))
   with Pre => Within (S, Text);

   function Valid (Text : String; S : Span; R : Rule; Encoding : Charset) return Boolean is
      Count  : Natural;
      Ok     : Boolean;
      Date   : Dates.Timestamp;
      Amount : Decimals.Decimal;
   begin
      if Is_Empty (S) then
         return False;
      end if;
      case R.Kind is
         when Text_Value =>
            Ofx.Text.Measure (Text, S, Encoding, True, Count, Ok);
            return Ok and then Count <= R.Max_Length;
         when Date_Value =>
            pragma Warnings (GNATprove, Off, "is set by", Reason => "Validation needs only Ok");
            Dates.Parse (Text, S, Date, Ok);
            pragma Warnings (GNATprove, On, "is set by");
            return Ok;
         when Amount_Value =>
            pragma Warnings (GNATprove, Off, "is set by", Reason => "Validation needs only Ok");
            Decimals.Parse (Text, S, Amount, Ok);
            pragma Warnings (GNATprove, On, "is set by");
            return Ok;
         when Number_Value =>
            return Length (S) <= R.Max_Length
              and then (for all I in S.First .. S.Last => Is_Digit (Text (I)));
         when Severity_Value =>
            return Matches (Text, S, "INFO") or else Matches (Text, S, "WARN")
              or else Matches (Text, S, "ERROR");
         when Trntype_Value =>
            return Is_Trntype (Text, S);
         when Accttype_Value =>
            return Matches (Text, S, "CHECKING") or else Matches (Text, S, "SAVINGS")
              or else Matches (Text, S, "MONEYMRKT") or else Matches (Text, S, "CREDITLINE");
         when Correctaction_Value =>
            return Matches (Text, S, "REPLACE") or else Matches (Text, S, "DELETE");
         when Inv401k_Value =>
            return Is_Inv401k (Text, S);
         when Code_Value =>
            return Length (S) = 3
              and then (for all I in S.First .. S.Last => Is_Upper (Text (I)));
      end case;
   end Valid;

end Ofx.Values;
