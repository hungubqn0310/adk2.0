"""
Tools for accessing MM Mega Market Việt Nam Vietnam information from JSON files.
Version with detailed content from links.
"""

from data.mm_data_index import (
    get_category_data, get_all_data,
    get_all_content, get_all_qa_pairs, search_mm_data
)
def get_all_mm_data():
    """
    Lấy toàn bộ dữ liệu MM Mega Market Việt Nam.
    Sử dụng công cụ này khi cần truy cập tất cả thông tin để phân tích và trả lời câu hỏi phức tạp.
    Đồng thời trả về link các trung tâm MM Mega Market Việt Nam theo miền Bắc, Trung, Nam.
    """
    data = get_all_data()

    region_links = {
        "Miền Bắc": "https://online.mmvietnam.com/store-locator?source=1",
        "Miền Trung": "https://online.mmvietnam.com/store-locator?source=2",
        "Miền Nam": "https://online.mmvietnam.com/store-locator?source=3",
    }

    return {
        "data": data,
        "region_links": region_links,
        "instruction": (
            "Anh chị có thể chọn khu vực để xem các trung tâm MM Mega Market Việt Nam gần nhất:\n"
            "- [MM Mega Market Việt Nam Miền Bắc](https://online.mmvietnam.com/store-locator?source=1)\n"
            "- [MM Mega Market Việt Nam Miền Trung](https://online.mmvietnam.com/store-locator?source=2)\n"
            "- [MM Mega Market Việt Nam Miền Nam](https://online.mmvietnam.com/store-locator?source=3)"
        )
    }

MM_GENERAL_INFO_CONTENT = {
    "url": "https://mmvietnam.com/mm-mega-market-2/",
    "title": "Giới thiệu về MM Mega Market Việt Nam và cung cấp thông tin liên hệ",
    "full_content": """Công ty TNHH MM Mega Market Việt Nam ("MMVN"), là thương hiệu chiến lược thuộc tập đoàn đa quốc gia BJC/TCC. Sau khi thay đổi nhận diện từ Metro Cash & Carry từ năm 2016, MM Mega Market Việt Nam (MMVN) tiếp tục phát triển mô hình kinh doanh bán lẻ giá sỉ xoay quanh triết lý 3C thể hiện tinh thần luôn Đặt khách hàng trong tim (Customer@Heart), Đổi mới không ngừng (Drive Changes) và Hợp tác để thành công (Collaboration).

Là nhà bán buôn bán lẻ tiên phong phát triển chuỗi cung ứng khép kín hiện đại và an toàn "từ Trang trại đến Bàn ăn", sau hơn 21 năm hoạt động và phát triển, MM Mega Market Việt Nam đã mở rộng thành 21 trung tâm Bán sỉ và Siêu Thị trên toàn quốc, cùng với 6 Trạm Thu mua và Phân phối hàng hóa, 8 Kho giao hàng (B2B), 2 Kho giao hàng trung tâm với gần 4000 nhân viên, 1500 nhà cung cấp và hàng trăm hộ nông dân đối tác trên toàn quốc.

Trước những thay đổi đáng kể trong hành vi mua sắm của khách hàng, MM Mega Market Việt Nam đã khởi động "Giá Tốt" nhằm chuyển đổi cửa hàng tạp hóa truyền thống sang mô hình bán lẻ hiện đại trên khắp Việt Nam. Hơn nữa, chúng tôi tập trung vào việc số hóa toàn bộ chuỗi cung ứng và hệ thống vận hành, đồng thời phát triển Click & Get trực tuyến cho khách hàng Hộ gia đình và nền tảng Thương mại điện tử dành cho khách hàng Chuyên nghiệp B2B bao gồm MM Pro và MM Mall với trải nghiệm mua sắm toàn diện.

Công ty đặt trách nhiệm xã hội của doanh nghiệp lên hàng đầu thông qua các hoạt động về nâng cao giáo dục, sức khỏe, đời sống cộng đồng, bảo vệ môi trường, và luôn hướng đến các giải pháp tiên phong, đồng hành cùng sự phát triển bền vững của Việt Nam.

TẦM NHÌN:
Trở thành đối tác bán lẻ được yêu thích nhất mọi lúc mọi nơi cho Khách hàng doanh nghiệp và Hộ gia đình tại Việt Nam.

SỨ MỆNH:
Chúng tôi đảm bảo sự hài lòng của khách hàng nhờ cung cấp sản phẩm chất lượng, với dịch vụ hoàn hảo thông qua mô hình bán hàng đa kênh. Đồng thời chúng tôi chủ động đóng góp vào sự phát triển xã hội, tăng trưởng bền vững của các bên liên quan thông qua đội ngũ nhân viên đầy nhiệt huyết.

THÔNG TIN CÔNG TY và THÔNG TIN LIÊN HỆ:
Công ty TNHH MM MEGA MARKET (VIỆT NAM)
Văn phòng chính: Khu B, Khu đô thị mới An Phú - An Khánh, Phường An Phú, TP. Thủ Đức, TP. Hồ Chí Minh, Việt Nam.
Điện thoại: 1800 646878
Email: contactus@mmvietnam.com
Chủ sở hữu: TCC LAND INTERNATIONAL (SINGAPORE) PTE.LTD
Giấy phép kinh doanh số 0302249586 do Sở Kế hoạch & Đầu tư TP.HCM cấp ngày 12-10-2018.""",
    
    "sections": {
        "company_info": """Công ty TNHH MM Mega Market Việt Nam ("MMVN"), là thương hiệu chiến lược thuộc tập đoàn đa quốc gia BJC/TCC. Sau khi thay đổi nhận diện từ Metro Cash & Carry từ năm 2016, MM Mega Market Việt Nam (MMVN) tiếp tục phát triển mô hình kinh doanh bán lẻ giá sỉ xoay quanh triết lý 3C thể hiện tinh thần luôn Đặt khách hàng trong tim (Customer@Heart), Đổi mới không ngừng (Drive Changes) và Hợp tác để thành công (Collaboration).""",
        
        "development": """Là nhà bán buôn bán lẻ tiên phong phát triển chuỗi cung ứng khép kín hiện đại và an toàn "từ Trang trại đến Bàn ăn", sau hơn 21 năm hoạt động và phát triển, MM Mega Market Việt Nam đã mở rộng thành 21 trung tâm Bán sỉ và Siêu Thị trên toàn quốc, cùng với 6 Trạm Thu mua và Phân phối hàng hóa, 8 Kho giao hàng (B2B), 2 Kho giao hàng trung tâm với gần 4000 nhân viên, 1500 nhà cung cấp và hàng trăm hộ nông dân đối tác trên toàn quốc.""",
        
        "digital_transformation": """Trước những thay đổi đáng kể trong hành vi mua sắm của khách hàng, MM Mega Market Việt Nam đã khởi động "Giá Tốt" nhằm chuyển đổi cửa hàng tạp hóa truyền thống sang mô hình bán lẻ hiện đại trên khắp Việt Nam. Hơn nữa, chúng tôi tập trung vào việc số hóa toàn bộ chuỗi cung ứng và hệ thống vận hành, đồng thời phát triển Click & Get trực tuyến cho khách hàng Hộ gia đình và nền tảng Thương mại điện tử dành cho khách hàng Chuyên nghiệp B2B bao gồm MM Pro và MM Mall với trải nghiệm mua sắm toàn diện.""",
        
        "social_responsibility": """Công ty đặt trách nhiệm xã hội của doanh nghiệp lên hàng đầu thông qua các hoạt động về nâng cao giáo dục, sức khỏe, đời sống cộng đồng, bảo vệ môi trường, và luôn hướng đến các giải pháp tiên phong, đồng hành cùng sự phát triển bền vững của Việt Nam.""",
        
        "vision": """Trở thành đối tác bán lẻ được yêu thích nhất mọi lúc mọi nơi cho Khách hàng doanh nghiệp và Hộ gia đình tại Việt Nam.""",
        
        "mission": """Chúng tôi đảm bảo sự hài lòng của khách hàng nhờ cung cấp sản phẩm chất lượng, với dịch vụ hoàn hảo thông qua mô hình bán hàng đa kênh. Đồng thời chúng tôi chủ động đóng góp vào sự phát triển xã hội, tăng trưởng bền vững của các bên liên quan thông qua đội ngũ nhân viên đầy nhiệt huyết.""",
        
        "contact_info": """THÔNG TIN CÔNG TY và THÔNG TIN LIÊN HỆ:
Công ty TNHH MM MEGA MARKET (VIỆT NAM)
Văn phòng chính: Khu B, Khu đô thị mới An Phú - An Khánh, Phường An Phú, TP. Thủ Đức, TP. Hồ Chí Minh, Việt Nam.
Điện thoại: 1800 646878  
Email: contactus@mmvietnam.com  
Chủ sở hữu: TCC LAND INTERNATIONAL (SINGAPORE) PTE.LTD  
Giấy phép kinh doanh số 0302249586 do Sở Kế hoạch & Đầu tư TP.HCM cấp ngày 12-10-2018."""
    }
}

MM_DELIVERY_POLICY_CONTENT = {
    "url": "https://mmvietnam.com/chinh-sach-giao-hang/",
    "title": "Chính sách giao hàng",
    "full_content": """Dịch vụ giao hàng miễn phí được áp dụng cho Khách hàng có đơn hàng đạt mức tối thiểu về giá trị và khoảng cách theo quy định của từng Trung tâm MM Mega Market Việt Nam.
Thời gian giao hàng chung của MM Mega Market Việt Nam:\n"
        "* Đơn hàng sẽ được giao trong vòng 4 giờ kể từ khi đặt hàng thành công.\n"
        "* Để nhận hàng trong ngày, anh/chị vui lòng đặt hàng trước 14:00 (2 giờ chiều).\n"
        "* Nếu đặt hàng sau 14:00, đơn hàng của anh/chị sẽ được giao vào ngày hôm sau.\n"
- Tại các Trung tâm (ngoại trừ MM Hưng Phú, MM Supermarket Thanh Xuân):
Miễn phí giao hàng cho đơn hàng từ 600.000đ trong phạm vi 7km.
Nếu khoảng cách giao hàng vượt quá 7km, phí vận chuyển là 5.000đ/km (tối đa 15km).
Đơn hàng dưới 600.000đ sẽ tính phí 30.000đ/đơn/7km, cộng thêm 5.000đ/km cho phần vượt quá (tối đa 15km).

- Tại MM Hưng Phú và MM Supermarket Thanh Xuân:
Miễn phí giao hàng cho đơn hàng từ 300.000đ trong phạm vi 7km.
Nếu vượt quá 7km, phí vận chuyển là 6.000đ/km (tối đa 15km).
Đơn hàng dưới 300.000đ sẽ tính phí 30.000đ/đơn/7km, cộng thêm 6.000đ/km cho phần vượt quá (tối đa 15km).

Lưu ý:
- Khách hàng vui lòng nhận hàng tại cổng, sảnh hoặc khu vực giao nhận của tòa nhà/chung cư/khu dân cư.
- Khoảng cách giao hàng được hệ thống xác định tự động từ Trung tâm MM Mega Market Việt Nam đến địa chỉ giao hàng. Phí giao hàng được hiển thị khi đặt hàng, khách hàng cần kiểm tra trước khi hoàn tất đơn.
- Riêng khách hàng ở quận 7 (TP.HCM) đặt hàng tại MM Mega Market Việt Nam An Phú: phí giao hàng tăng thêm 12.000đ cho phần quãng đường vượt 2km ngoài phạm vi tiêu chuẩn.
- Sản phẩm Kem & Bánh đông lạnh:
  + Chỉ áp dụng cho đơn hàng đã thanh toán trước.
  + Áp dụng phạm vi tối đa 7km và giá trị tối thiểu 600.000đ (hoặc 300.000đ tại MM Hưng Phú & MM Supermarket Thanh Xuân).
- Sản phẩm nặng hoặc cồng kềnh: phụ thu thêm 140.000đ trong phạm vi 7-10km. Nhân viên MM Mega Market Việt Nam sẽ liên hệ để xác nhận chi phí vận chuyển.
- Hàng được coi là nặng/cồng kềnh khi kích thước >0.34 m³ hoặc trọng lượng >90kg.
- Đơn hàng có tổng giá trị trên 20 triệu (trước thuế) yêu cầu thanh toán chuyển khoản trực tiếp. Khách hàng vui lòng liên hệ để được hỗ trợ.

Xác nhận đơn hàng:
Với mỗi đơn hàng được tiếp nhận, Nhân viên chăm sóc khách hàng của MM Mega Market Việt Nam sẽ gọi điện thoại xác nhận và gửi email hoặc tin nhắn (zalo) vào địa chỉ mà Khách hàng đã cung cấp.
Quý khách có thể theo dõi tình trạng đơn hàng tại mục “Theo dõi đơn hàng” (Khách Hàng cần cung cấp mã đơn hàng hoặc email dùng để mua hàng).
Trường hợp MM Mega Market Việt Nam gọi điện 03 lần không thành công, đơn hàng sẽ tự động hủy.
Nếu hàng đã được vận chuyển đến đúng địa chỉ nhưng Quý khách không nhận hàng, hàng hóa và hóa đơn sẽ được hoàn trả về trung tâm để hủy. Nếu khách hàng từ chối nhận hàng trên 3 lần, MM Mega Market Việt Nam có quyền từ chối phục vụ cho lần sau.

Nhận hàng và thanh toán:
MM Mega Market Việt Nam khuyến khích Khách hàng kiểm tra số lượng và chất lượng hàng hóa ngay khi nhận hàng. Với các sản phẩm được đóng gói từ nhà sản xuất, Quý khách vui lòng không mở niêm phong.
Sau khi xác nhận đúng đơn hàng, Quý khách vui lòng ký nhận và thanh toán bằng tiền mặt trước khi nhận hàng.""",

    "sections": {
        "general": """Dịch vụ giao hàng miễn phí được áp dụng cho Khách hàng có đơn hàng đạt mức tối thiểu về giá trị và khoảng cách theo quy định của từng Trung tâm MM Mega Market Việt Nam.""",
        
        "standard_centers": """Tại các Trung tâm (ngoại trừ MM Hưng Phú, MM Supermarket Thanh Xuân):
- Miễn phí giao hàng cho đơn hàng từ 600.000đ trong phạm vi 7km.
- Nếu khoảng cách giao hàng vượt quá 7km, phí vận chuyển là 5.000đ/km (tối đa 15km).
- Đơn hàng dưới 600.000đ: tính 30.000đ/đơn/7km, cộng thêm 5.000đ/km cho phần vượt quá (tối đa 15km).""",
        
        "hungphu_thanhxuan": """Tại MM Hưng Phú và MM Supermarket Thanh Xuân:
- Miễn phí giao hàng cho đơn hàng từ 300.000đ trong phạm vi 7km.
- Nếu vượt quá 7km, phí vận chuyển là 6.000đ/km (tối đa 15km).
- Đơn hàng dưới 300.000đ: tính 30.000đ/đơn/7km, cộng thêm 6.000đ/km cho phần vượt quá (tối đa 15km).""",
        
        "notes": """Khách hàng vui lòng nhận hàng tại cổng, sảnh hoặc khu vực giao nhận của tòa nhà/chung cư/khu dân cư.
Khoảng cách giao hàng được hệ thống xác định tự động và phí sẽ hiển thị trên trang đặt hàng.
Riêng khách hàng ở quận 7 (TP.HCM) đặt tại MM An Phú, phí giao hàng tăng thêm 12.000đ cho phần vượt 2km.""",
        
        "special_products": """Sản phẩm Kem & Bánh đông lạnh:
- Chỉ áp dụng cho đơn hàng đã thanh toán trước.
- Áp dụng phạm vi tối đa 7km và giá trị tối thiểu 600.000đ (hoặc 300.000đ tại MM Hưng Phú & MM Supermarket Thanh Xuân).""",
        
        "heavy_items": """Sản phẩm nặng hoặc cồng kềnh: phụ thu thêm 140.000đ trong phạm vi 7-10km.
Hàng được coi là nặng/cồng kềnh khi kích thước >0.34 m³ hoặc trọng lượng >90kg.
Nhân viên MM sẽ liên hệ trực tiếp để xác nhận phí vận chuyển.""",
        
        "high_value_orders": """Đơn hàng có tổng giá trị trên 20 triệu (trước thuế) yêu cầu thanh toán chuyển khoản trực tiếp. Khách hàng vui lòng liên hệ để được hỗ trợ.""",

        "order_confirmation": """Với mỗi đơn hàng được tiếp nhận, nhân viên chăm sóc khách hàng của Mega Market Việt Nam sẽ gọi điện xác nhận và gửi email hoặc tin nhắn (zalo) đến địa chỉ khách hàng cung cấp.
Khách hàng có thể theo dõi tình trạng đơn tại mục “Theo dõi đơn hàng”.
Nếu Mega Market Việt Nam gọi 3 lần không thành công, đơn hàng sẽ tự động hủy.
Nếu hàng đã giao đúng địa chỉ nhưng khách không nhận, đơn và hóa đơn sẽ bị hủy. Với khách hàng từ chối nhận hàng trên 3 lần, MM có quyền từ chối phục vụ.""",
        
        "receiving_and_payment": """MM Mega Market Việt Nam khuyến khích khách hàng kiểm tra hàng hóa khi nhận.
Các sản phẩm đã đóng gói sẵn không nên mở niêm phong.
Sau khi xác nhận đúng đơn hàng, khách vui lòng ký nhận và thanh toán tiền mặt để hoàn tất giao dịch."""
    }
}

MM_RETURN_EXCHANGE_POLICY_CONTENT = {
    "url": "https://online.mmvietnam.com/faq/chinh-sach-doi-tra",  # nếu có link chính thức thì thay vào
    "title": "Chính sách đổi - trả và bảo hành hàng hóa tại MM Mega Market Việt Nam",
    "full_content": """Chính sách đổi - trả hàng
----------------------------------
Sản phẩm phải thỏa mãn một trong các điều kiện dưới đây để được đổi - trả:

1. Sản phẩm đã mua gặp phải vấn đề  kỹ thuật/chất lượng trong vòng 3 ngày kể từ khi mua hàng. 
   Riêng những mặt hàng thực phẩm khô/tươi phải được đổi trả trong vòng 24 giờ.

2. Sản phẩm đã mua gặp phải vấn đề  kỹ thuật/chất lượng không thể  sửa chữa được trong thời hạn bảo hành 
   (sản phẩm đã qua hơn 3 lần bảo hành trong thời hạn bảo hành).

3. Sản phẩm có dấu hiệu hư hỏng, dập nát hoặc không đúng với thông tin đơn hàng đã đặt 
   (người nhận hàng, tên mã sản phẩm, số lượng,…) được phát hiện lúc kiểm tra ngay sau khi nhận hàng.

**Các trường hợp không áp dụng đổi - trả hàng:**

- Những mặt hàng thực phẩm *Tươi sống* sẽ không được đổi - trả hàng, trừ trường hợp hàng hóa hư hỏng 
  được phát hiện lúc kiểm tra ngay khi nhận hàng.

- Những mặt hàng *khuyến mãi* sẽ không được áp dụng đổi - trả, trừ trường hợp hàng khuyến mãi gặp vấn đề 
  kỹ thuật/chất lượng/số lượng như liệt kê ở điều kiện trên. 
  MM Mega Market Việt Nam sẽ tiến hành *1 đổi 1* đối với hàng khuyến mãi đó.

(*): Các mặt hàng thực phẩm **Tươi sống** bao gồm các thực phẩm tươi như các loại thịt, cá, hải sản, rau củ, trái cây, hoa tươi, cây cảnh, dụng cụ làm vườn

----------------------------------
Chính sách bảo hành
----------------------------------
Những mặt hàng điện tử mua tại MM Mega Market Việt Nam đều được bảo hành bởi các nhà sản xuất, 
địa điểm bảo hành do nhà sản xuất ủy quyền được ghi rõ trên phiếu bảo hành.

Ngoài ra, Quý khách có thể mang sản phẩm đến MM Mega Market Việt Nam để được tư vấn bảo hành.

----------------------------------
Phương thức đổi - trả hàng
----------------------------------
- Nếu Quý khách trả **một phần đơn hàng** ngay khi nhận:  
  Nhân viên giao hàng sẽ ghi chú trên bản sao đơn hàng và liên 2 của hóa đơn, sau đó yêu cầu khách hàng ký xác nhận.  
  Nhân viên giao hàng sẽ thu tiền tương ứng với lượng hàng khách hàng đồng ý nhận.  
  Những sản phẩm bị trả lại cùng với hóa đơn và bản sao đơn hàng sẽ được đem về trung tâm MM Mega Market Việt Nam.  
  Hóa đơn chính xác sẽ được xuất lại và mang đến cho khách hàng vào lần mua sau đó hoặc gửi qua đường bưu điện.

- Nếu Quý khách **không nhận toàn bộ đơn hàng**:  
  Hàng hóa và hóa đơn sẽ được vận chuyển về trung tâm và tiến hành hủy hóa đơn.

- Với sản phẩm **lỗi kỹ thuật/chất lượng cần đổi - trả**:  
  Quý khách vui lòng mang sản phẩm và hóa đơn đến trung tâm MM Mega Market Việt Nam đã mua hàng để được hỗ trợ.
""",

    "sections": {
        "exchange_policy": """Sản phẩm được đổi - trả nếu:
1. Gặp lỗi kỹ thuật/chất lượng trong 3 ngày (24h với hàng tươi sống/khô).
2. Lỗi kỹ thuật không thể sửa trong thời hạn bảo hành (sau hơn 3 lần bảo hành).
3. Hư hỏng, sai thông tin đơn hàng khi kiểm tra lúc nhận.

Không áp dụng đổi - trả cho:
- Hàng tươi sống (trừ khi phát hiện lúc nhận hàng có thể đổi trả).
- Hàng khuyến mãi (trừ khi bị lỗi kỹ thuật/chất lượng/số lượng).""",

        "warranty_policy": """Hàng điện tử được bảo hành bởi nhà sản xuất, địa điểm bảo hành được ghi rõ trên phiếu bảo hành. 
Khách hàng có thể mang sản phẩm đến MM Mega Market Việt Nam để được tư vấn bảo hành.""",

        "return_method": """Trả **một phần đơn hàng**: nhân viên giao hàng ghi chú, khách ký xác nhận, thu tiền tương ứng.  
Sản phẩm trả lại mang về trung tâm, hóa đơn được xuất lại và gửi lần sau hoặc qua bưu điện.  

 Không nhận **toàn bộ đơn hàng**: hàng và hóa đơn được chuyển về trung tâm, hóa đơn bị hủy.  

Sản phẩm **lỗi kỹ thuật/chất lượng**: Anh/chị vui lòng liên hệ cho nhân viên chăm sóc khách hàng của MM Mega Market Việt Nam để hỗ trợ đổi/trả."""
    }
}

MM_MCARD_POLICY_CONTENT = {
    "url": "https://online.mmvietnam.com/blog/thong-bao-thay-doi-cach-tinh-diem-mcard",
    "title": "Chính sách thẻ thành viên MCard và cách tính điểm mới",
    "full_content": """M Card là chương trình khách hàng thân thiết của MM Mega Market Việt Nam, mang đến nhiều ưu đãi và quyền lợi đặc biệt cho các thành viên.

Khi sở hữu M Card, khách hàng được:
- Tích điểm thưởng khi mua sắm và đổi điểm lấy quà, ưu đãi hấp dẫn.
- Nhận các chương trình khuyến mãi, giảm giá đặc biệt dành riêng cho thành viên.
- Hưởng quà tặng sinh nhật, tham gia sự kiện riêng của thành viên.
- Cập nhật thông tin khuyến mãi nhanh chóng và tiện lợi.

Từ ngày 01/10/2025, MCard sẽ áp dụng **cách tính điểm hoàn toàn mới**:

- **Trước:** 10.000 VND = 1 điểm MCard; 1 điểm = 50 đồng  
- **Nay:** 10.000 VND = 50 điểm MCard; 1 điểm = 1 đồng  

Điểm cũ của Quý khách trước ngày 01/10/2025 sẽ được tự động nhân lên 50 lần tương ứng với cơ chế mới.

**Ví dụ:**  
Đến ngày 30/09/2025, tài khoản MCard của Quý khách có 2.000 điểm.  
Sau ngày 01/10, tài khoản sẽ được tự động nhân lên 50 lần, tương đương **100.000 điểm** hiển thị trên ứng dụng MCard.

**Điểm tích từ hóa đơn mới:**  
Từ ngày 01/10/2025, khi Quý khách mua hóa đơn trị giá 10.000.000 VND, Quý khách sẽ tích được **50.000 điểm (tương đương 50.000 đồng)**.

MCard - Tích điểm dễ hiểu, đổi quà dễ dàng.""",

    "sections": {
        "introduction": """M Card là chương trình khách hàng thân thiết của MM Mega Market Việt Nam, mang đến nhiều ưu đãi và quyền lợi đặc biệt cho các thành viên.""",

        "benefits": """- Tích điểm thưởng khi mua sắm và đổi điểm lấy quà, ưu đãi hấp dẫn.
- Nhận các chương trình khuyến mãi, giảm giá đặc biệt dành riêng cho thành viên.
- Hưởng quà tặng sinh nhật, tham gia sự kiện riêng của thành viên.
- Cập nhật thông tin khuyến mãi nhanh chóng và tiện lợi.""",

        "point_calculation_update": """Từ ngày 01/10/2025, MCard sẽ áp dụng cách tính điểm hoàn toàn mới:
- Trước: 10.000 VND = 1 điểm MCard; 1 điểm = 50 đồng
- Nay: 10.000 VND = 50 điểm MCard; 1 điểm = 1 đồng

Điểm cũ của khách hàng trước ngày 01/10/2025 sẽ được tự động nhân lên 50 lần theo cơ chế mới.""",

        "example": """Ví dụ:
Đến ngày 30/09/2025, tài khoản MCard có 2.000 điểm.
Sau ngày 01/10/2025, hệ thống tự động nhân lên 50 lần, tương đương 100.000 điểm.""",

        "invoice_points": """Từ ngày 01/10/2025, khi khách hàng mua hóa đơn trị giá 10.000.000 VND, sẽ tích được 50.000 điểm (tương đương 50.000 đồng).""",

        "slogan": """MCard - Tích điểm dễ hiểu, đổi quà dễ dàng."""
    }
}
MM_PRODUCT_QUALITY_CONTENT = {
    "url": "https://online.mmvietnam.com/faq/quan-ly-chat-luong",
    "title": "Tiêu chuẩn chất lượng sản phẩm",
    "full_content": """MM Mega Market là chuỗi siêu thị thực phẩm tươi sống được ưa chuộng hàng đầu Việt Nam. 
Tại đây, các mặt hàng được bày bán phải đạt chuẩn chứng nhận.""",
    "sections": {
        "introduction": "MM Mega Market là chuỗi siêu thị thực phẩm tươi sống được ưa chuộng hàng đầu Việt Nam.",
        "standards": "Các mặt hàng được bày bán tại hệ thống MM phải đạt các tiêu chuẩn chứng nhận chất lượng nghiêm ngặt.",
        "link": "https://online.mmvietnam.com/faq/quan-ly-chat-luong"
    }
}

MM_PRIVACY_POLICY_CONTENT = {
    "url": "https://mmvietnam.com/chinh-sach-bao-mat/",
    "title": "Chính sách Bảo mật Thông tin Khách hàng",
    "full_content": """MM Mega Market Việt Nam cam kết bảo vệ tuyệt đối thông tin cá nhân của khách hàng trong quá trình mua sắm và sử dụng dịch vụ.

Bằng việc đọc và hiểu rõ Chính sách Bảo mật này, khách hàng sẽ nắm rõ cách thức mà thông tin cá nhân của khách hàng được thu thập, sử dụng, bảo vệ hoặc xử lý tại website MM Mega Market Việt Nam.

### Nguyên tắc thu thập và sử dụng thông tin
- Thông tin cá nhân được thu thập chỉ nhằm mục đích phục vụ đơn hàng, chăm sóc khách hàng và nâng cao chất lượng dịch vụ.
- MM Mega Market Việt Nam không chia sẻ, trao đổi hoặc bán thông tin khách hàng cho bất kỳ bên thứ ba nào nếu không có sự đồng ý của khách hàng.

### Những thông tin cá nhân được thu thập
Khi bạn đăng ký nhận bản tin từ MM Mega Market Việt Nam, bạn có thể được yêu cầu cung cấp các thông tin như: họ tên, địa chỉ email hoặc các dữ liệu khác nhằm hỗ trợ MM Mega Market Việt Nam trong việc cải thiện chất lượng dịch vụ và thông tin gửi đến khách hàng.

### Thời điểm thu thập thông tin
MM Mega Market Việt Nam chỉ thu thập thông tin khi bạn:
- Đăng ký thành viên.
- Đăng ký nhận bản tin định kỳ.

### Thời gian lưu trữ thông tin
Thông tin cá nhân được lưu trữ kể từ khi bạn đăng ký hoặc cung cấp thông tin cho MM Mega Market Việt Nam qua các tính năng trên website. Dữ liệu sẽ được lưu giữ cho đến khi bạn yêu cầu MM Mega Market Việt Nam hủy hoặc xóa các thông tin này.

### Mục đích sử dụng thông tin
MM Mega Market Việt Nam có thể sử dụng thông tin cá nhân thu thập được cho các mục đích sau:
- Nâng cao chất lượng nội dung và trải nghiệm trên website.
- Gửi email định kỳ về các chương trình khuyến mãi, ưu đãi, hoặc thông tin sản phẩm.
- Thực hiện khảo sát marketing hoặc nghiên cứu hành vi người dùng.

### Bảo vệ thông tin khách hàng
MM Mega Market Việt Nam cam kết bảo vệ thông tin cá nhân của khách hàng.  
Hiện website của MM Mega Market Việt Nam không sử dụng chứng chỉ SSL vì chỉ cung cấp thông tin và **không bao giờ chủ động yêu cầu khách hàng cung cấp dữ liệu mang tính riêng tư**.

### Việc sử dụng 'cookie'
MM Mega Market Việt Nam **không sử dụng cookie cho các mục đích theo dõi**.  
Khách hàng có thể tùy chọn để trình duyệt cảnh báo mỗi khi cookie được gửi đi hoặc tắt hoàn toàn cookie nếu muốn.

### Quyền của khách hàng đối với thông tin cá nhân
- Khách hàng có quyền cung cấp hoặc từ chối cung cấp thông tin cá nhân cho MM Mega Market Việt Nam.
- Khách hàng có quyền kiểm tra, cập nhật, chỉnh sửa thông tin của khách hàng bằng cách đăng nhập tài khoản hoặc liên hệ trực tiếp với MM Mega Market Việt Nam.
- Trường hợp khách hàng muốn yêu cầu xóa dữ liệu cá nhân, vui lòng liên hệ với MM Mega Market Việt Nam để được hỗ trợ.

### Tuân thủ pháp luật
MM Mega Market Việt Nam luôn tuân thủ quy định pháp luật Việt Nam về bảo mật và an toàn thông tin, đảm bảo quyền riêng tư tối đa cho khách hàng.

""",
    "sections": {
        "introduction": "MM Mega Market Việt Nam cam kết bảo mật tuyệt đối thông tin cá nhân của khách hàng và minh bạch trong cách thu thập, sử dụng dữ liệu.",
        "data_collection": "Thông tin được thu thập khi khách hàng đăng ký thành viên hoặc nhận bản tin, chỉ nhằm phục vụ đơn hàng và nâng cao dịch vụ.",
        "data_storage": "Dữ liệu được lưu trữ cho đến khi khách hàng yêu cầu hủy hoặc xóa khỏi hệ thống.",
        "data_usage": "Thông tin có thể được dùng để gửi bản tin khuyến mãi, khảo sát marketing hoặc cải thiện nội dung website.",
        "data_protection": "MM Mega Market Việt Nam cam kết bảo vệ thông tin khách hàng, không chia sẻ cho bên thứ ba khi chưa có sự đồng ý.",
        "cookie_policy": "MM Mega Market Việt Nam không sử dụng cookie để theo dõi người dùng; khách hàng có thể tắt cookie nếu muốn.",
        "customer_rights": "Khách hàng có quyền xem, chỉnh sửa, hoặc yêu cầu xóa dữ liệu cá nhân bất cứ lúc nào.",
        "link": "https://mmvietnam.com/chinh-sach-bao-mat/"
    }
}


MM_LEGAL_INFO_CONTENT = {
    "url": "https://mmvietnam.com/thong-tin-phap-ly-va-dieu-khoan-su-dung/",
    "title": "Thông Tin Pháp Lý & Điều Khoản Sử Dụng",
    "full_content": """**Công ty TNHH MM Mega Market Việt Nam (VIỆT NAM)**  
Phòng Truyền Thông - Văn phòng chính: Khu B, Khu Đô thị mới An Phú - An Khánh, Phường An Phú, Quận 2, Tp. Hồ Chí Minh, Việt Nam.  
Điện thoại: +84 (8) 35 190 390 - 6401.  
Fax: +84 (8) 35 190 370.  

---

### 1. Điều Khoản Sử Dụng
MM Mega Market Việt Nam (Việt Nam) luôn nỗ lực để cung cấp các thông tin chính xác và đầy đủ trên trang web. Tuy nhiên, MM không chịu trách nhiệm về tính thời sự, độ chính xác hoặc tính toàn vẹn của các thông tin, bao gồm cả các liên kết ngoài dẫn đến trang web khác.  
MM bảo lưu quyền điều chỉnh, bổ sung thông tin mà không cần thông báo trước.  

Nội dung của trang web được bảo hộ bản quyền. Người dùng được phép lưu trữ hoặc sao chép bản văn, nhưng **việc sao chép hình ảnh, đồ họa vượt quá phạm vi hiển thị trên màn hình là không được phép**.  

---

### 2. Bảo Hộ Bản Quyền và Nhãn Hiệu
MM tuân thủ đầy đủ quy định về bản quyền khi sử dụng các đồ họa, phim ảnh và văn bản.  
Các nhãn hiệu, thương hiệu được sử dụng có thể thuộc quyền bảo hộ của bên thứ ba.  
Mọi hành vi sao chép hoặc sử dụng lại nội dung, hình ảnh, hoặc tài liệu từ website **phải được sự cho phép bằng văn bản** của MM Mega Market Việt Nam.  

---

### 3. Hiệu Lực Pháp Lý của Miễn Trừ Trách Nhiệm
Điều khoản này được xem là một phần của trang Internet dẫn đến website của MM.  
Nếu bất kỳ phần nào không còn tuân thủ pháp luật hiện hành, các phần còn lại của văn bản vẫn giữ nguyên hiệu lực.  

---

### 4. Thu Nhận và Xử Lý Thông Tin Cá Nhân
- Thông tin cá nhân chỉ được ghi nhận khi người dùng **tự nguyện cung cấp** (ví dụ: gửi yêu cầu, đánh dấu trang, liên hệ).  
- MM lưu giữ và xử lý dữ liệu tuân thủ các quy định pháp luật liên quan đến bảo vệ dữ liệu cá nhân.  
- Thông tin được dùng trong nội bộ MM để cung cấp sản phẩm, dịch vụ theo yêu cầu của khách hàng.  
- Khách hàng có thể yêu cầu **xóa thông tin cá nhân** bất cứ lúc nào bằng văn bản.  
- Website của MM có sử dụng **cookies** nhằm tối ưu trải nghiệm người dùng.  

---

### 5. Thông Tin Doanh Nghiệp
**Công ty TNHH MM Mega Market Việt Nam (VIỆT NAM)**  
Giấy Chứng Nhận Đăng ký Doanh nghiệp số **0302249586**, đăng ký thay đổi lần thứ 9 ngày **25/01/2016** do **Sở Kế Hoạch và Đầu Tư TP. Hồ Chí Minh** cấp.  
(Tên cũ: Công ty TNHH METRO Cash & Carry Việt Nam)  
**Mã Số Thuế:** 0302249586  

Phòng Truyền Thông - Văn phòng chính:  
Khu B, Khu Đô thị mới An Phú - An Khánh, Phường An Phú, Quận 2, TP. Hồ Chí Minh, Việt Nam.  
Điện thoại: +84 (8) 35 190 390 - 6401.  
Fax: +84 (8) 35 190 370.  
""",
    "sections": {
        "introduction": "Công ty TNHH MM Mega Market Việt Nam (Việt Nam) cung cấp thông tin pháp lý và điều khoản sử dụng minh bạch cho khách hàng và đối tác.",
        "usage_terms": "Người dùng được phép sử dụng thông tin trên website trong phạm vi cho phép, không được sao chép hình ảnh hoặc nội dung vượt quá giới hạn pháp luật.",
        "copyright": "Tất cả nội dung, hình ảnh và thương hiệu thuộc quyền sở hữu của MM Mega Market Việt Nam hoặc các bên được cấp phép.",
        "privacy": "Thông tin cá nhân của khách hàng được thu thập và xử lý theo quy định pháp luật; MM tuân thủ bảo mật dữ liệu và sử dụng cookies để cải thiện dịch vụ.",
        "legal_effect": "Điều khoản miễn trừ trách nhiệm là một phần không tách rời của trang web; phần còn lại vẫn giữ hiệu lực nếu một phần bị vô hiệu.",
        "company_info": "Doanh nghiệp đăng ký tại TP. Hồ Chí Minh, MST 0302249586, văn phòng tại Khu Đô thị An Phú - An Khánh, Quận 2, TP.HCM."
    }
}
MM_PURCHASE_INSTRUCTIONS_CONTENT = {
    "url": "https://online.mmvietnam.com/faq/huong-dan-mua-hang",
    "title": "Hướng Dẫn Mua Hàng",
    "full_content": """**Hướng Dẫn Mua Hàng Tại MM Mega Market Việt Nam**

Anh/Chị có thể dễ dàng mua hàng trực tuyến tại MM Mega Market Việt Nam theo các bước sau:

### 1. Duyệt và chọn sản phẩm
- Truy cập website hoặc ứng dụng **MM Mega Market Việt Nam Online**.
- Duyệt qua các danh mục sản phẩm hoặc sử dụng thanh tìm kiếm để tìm sản phẩm mong muốn.
- Nhấn **"Thêm vào giỏ hàng"** đối với các sản phẩm Anh/Chị muốn mua.

### 2. Kiểm tra giỏ hàng
- Sau khi chọn xong, truy cập mục **Giỏ hàng** để xem lại danh sách sản phẩm.
- Có thể thay đổi số lượng, xóa hoặc thêm sản phẩm khác trước khi thanh toán.

### 3. Thanh toán (Xuất hóa đơn)
-100% đơn hàng đều được xuất hoá đơn, nếu anh/chị muốn xuất hoá đơn cho doanh nghiệp vui lòng:
  - Chọn mục “Xuất hóa đơn VAT” trong trang thanh toán.
  - Nhập thông tin xuất hóa đơn (tên công ty, mã số thuế, địa chỉ).
  - Hóa đơn điện tử sẽ được nhận khi đơn hàng được giao thành công.
- Lựa chọn **phương thức thanh toán** phù hợp:
  - Tiền mặt khi nhận hàng (**COD**)
  - Thẻ ngân hàng (**ATM/Visa/MasterCard**)
  - Ví điện tử (**Momo**, **ZaloPay**)
  - Hoặc các phương thức thanh toán trực tuyến khác theo cập nhật của hệ thống.
- Đối với Sản phẩm Kem & Bánh đông lạnh:
  + Chỉ áp dụng cho đơn hàng đã thanh toán trước.
  + Áp dụng phạm vi tối đa 7km và giá trị tối thiểu 600.000đ (hoặc 300.000đ tại MM Hưng Phú & MM Supermarket Thanh Xuân).
### 4. Xác nhận đơn hàng
- Sau khi hoàn tất thanh toán, Anh/Chị sẽ nhận được email hoặc tin nhắn xác nhận đơn hàng từ MM Mega Market Việt Nam.

### 5. Nhận hàng
- Nhân viên MM Mega Market Việt Nam sẽ liên hệ để giao hàng theo địa chỉ đã đăng ký.
- Anh/Chị vui lòng kiểm tra hàng hóa trước khi ký nhận.

Để xem thêm chi tiết, vui lòng truy cập [Hướng Dẫn Mua Hàng](https://online.mmvietnam.com/faq/huong-dan-mua-hang).
""",
    "sections": {
        "overview": "Hướng dẫn khách hàng từng bước mua sắm trực tuyến tại MM Mega Market Việt Nam, từ việc chọn hàng đến thanh toán và nhận hàng.",
        "browse_products": "Duyệt qua danh mục sản phẩm hoặc dùng thanh tìm kiếm để tìm kiếm sản phẩm mong muốn.",
        "cart_review": "Truy cập giỏ hàng để xem lại, điều chỉnh hoặc xóa sản phẩm trước khi thanh toán.",
        "payment_methods": "Chấp nhận thanh toán bằng tiền mặt (COD), thẻ ngân hàng, ví Momo, ZaloPay và các hình thức khác.",
        "order_confirmation": "Khách hàng nhận email hoặc tin nhắn xác nhận sau khi đặt hàng thành công.",
        "delivery": "Nhân viên MM Mega Market Việt Nam sẽ liên hệ giao hàng theo địa chỉ đã cung cấp. Khách hàng nên kiểm tra hàng hóa trước khi ký nhận."
    }
}

def mmvn_purchase_instructions():
    """
    Lấy thông tin chung về hướng dẫn mua hàng, các phương thức thanh toán, và mọi thứ liên quan đến hóa đơn.
    Khi người dùng hỏi về hóa đơn (bill), hệ thống phải hiển thị CTA button để hỗ trợ thanh toán hoặc xem chi tiết hóa đơn.
    """
    info = MM_PURCHASE_INSTRUCTIONS_CONTENT

    content_with_link = (
        f"{info['full_content']}\n\n"
        f"Xem thêm chi tiết tại: [{info['title']}]({info['url']})"
    )

    return {
        "content": content_with_link,
        "url": info["url"],
        "title": info["title"],
        "show_proceed_to_checkout_cta_button": True,
        "instruction_for_agent": (
            "Respond to user with the purchase/invoice information from the 'content' field. "
            "show_proceed_to_checkout_cta_button=true to display the 'Thanh toán ngay' button."
        )
    }

def mmvn_faq_get_general_info():
    """
    Lấy thông tin chung về MM MMega Market Việt Nam.
    Trả về nội dung chi tiết từ trang giới thiệu chính thức.
    """
    info = MM_GENERAL_INFO_CONTENT

    
    content_with_link = (
        f"{info['full_content']}\n\n"
        f" Xem thêm chi tiết tại: [{info['title']}]({info['url']})"
    )

    return {
        "content": content_with_link,
        "url": info["url"],
        "title": info["title"]
    }
def mmvn_faq_get_free_ship_policy():
    """
    Lấy thông tin về chính sách miễn phí giao hàng của MM Mega Market Việt Nam (MMVN).
    Bao gồm: phạm vi áp dụng, điều kiện để được miễn phí giao hàng, và hướng dẫn kiểm tra giá trị đơn hàng.
    Trả về nội dung chi tiết kèm đường dẫn chính thức.
    """
    info = MM_DELIVERY_POLICY_CONTENT

    content_with_link = (
        f"{info['full_content']}\n\n"
        f"Xem thêm chi tiết tại: [{info['title']}]({info['url']})"
    )

    return {
        "content": content_with_link,
        "url": info["url"],
        "title": info["title"],
        "instruction_for_agent": (
            "You need to check the user's cart 'grand_total' in 'view_cart' and respond in a single message. "
            "If the total amount meets or exceeds MMVN's free shipping threshold"
            "inform the user that their order qualifies for free delivery. "
            "Otherwise, explain the delivery fee policy and suggest adding more items to reach the free shipping limit."
        ),
    }
def mmvn_faq_get_exchange_policy():
    """
    Lấy thông tin chung về chính sách đổi trả hàng, bảo hành.
    Trả về nội dung chi tiết từ trang giới thiệu chính thức.
    """
    info = MM_RETURN_EXCHANGE_POLICY_CONTENT

    
    content_with_link = (
        f"{info['full_content']}\n\n"
        f" Xem thêm chi tiết tại: [{info['title']}]({info['url']})"
    )

    return {
        "content": content_with_link,
        "url": info["url"],
        "title": info["title"]
    }
def mmvn_faq_get_mcard_info():
    """
    Lấy thông tin chung về thông tin, cách tính điểm của Mcard.
    """
    info = MM_MCARD_POLICY_CONTENT

    
    content_with_link = (
        f"{info['full_content']}\n\n"
        f" Xem thêm chi tiết tại: [{info['title']}]({info['url']})"
    )

    return {
        "content": content_with_link,
        "url": info["url"],
        "title": info["title"]
    }
def mmvn_promotional_publications():
    """Lấy thông tin về các ấn phẩm khuyến mãi"""
    return("""Em có thể cung cấp thông tin về các ấn phẩm khuyến mãi hiện tại của MM Mega Market Việt Nam. 
Để xem chi tiết, Anh/Chị vui lòng:
- Nhấn vào mục **“Khuyến mãi”** trên thanh menu của website, hoặc  
- Truy cập trực tiếp tại [Ấn phẩm khuyến mãi](https://mmvietnam.com/an-pham-khuyen-mai/)
    """)




def mmvn_product_quality():
    """
    Lấy thông tin về chất lượng sản phẩm của MM Mega Market Việt Nam
    """
    info = MM_PRODUCT_QUALITY_CONTENT

    
    content_with_link = (
        f"{info['full_content']}\n\n"
        f" Xem thêm chi tiết tại: [{info['title']}]({info['url']})"
    )

    return {
        "content": content_with_link,
        "url": info["url"],
        "title": info["title"]
    }
def mmvn_privacy_policy():
    """
    Lấy thông tin về các chính sách bảo mật thông tin của người dùng
    """
    info = MM_PRIVACY_POLICY_CONTENT

    
    content_with_link = (
        f"{info['full_content']}\n\n"
        f" Xem thêm chi tiết tại: [{info['title']}]({info['url']})"
    )

    return {
        "content": content_with_link,
        "url": info["url"],
        "title": info["title"]
    }
def mmvn_Legal_and_terms_of_use():
    """Lấy thông tin về pháp lý và điều khoản sử dụng của MM Mega Market Việt Nam"""
    info = MM_LEGAL_INFO_CONTENT

    
    content_with_link = (
        f"{info['full_content']}\n\n"
        f" Xem thêm chi tiết tại: [{info['title']}]({info['url']})"
    )

    return {
        "content": content_with_link,
        "url": info["url"],
        "title": info["title"]
    }
def mmvn_redirect_customer_care():
    """Hướng dẫn người dùng bấm nút hỗ trợ ngay bên dưới chatbox để được nhận tư vấn từ nhân viên chăm sóc khách hàng"""
    return("""Anh/chị vui lòng liên hệ trực tiếp với bộ phận Chăm sóc khách hàng để bên em xử lý ạ!""")